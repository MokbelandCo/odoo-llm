"""Provider-neutral speech-to-text contract.

Application code calls ``llm.model.transcribe()`` for batch/file STT and the
``transcribe_live_*`` methods for a persistent realtime session. Provider
modules implement ``<service>_transcribe()`` / ``<service>_transcribe_live_*``.
Storage keys, signed URLs and other application references must never enter
this layer.
"""

import logging
import uuid

from odoo import _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

TRANSCRIPTION_ERROR_CODES = (
    "unsupported_capability",
    "empty_audio",
    "invalid_audio",
    "unsupported_format",
    "size_limit",
    "authentication",
    "throttling",
    "provider_failure",
)

#: Permanent input problems: retrying identical bytes cannot succeed.
PERMANENT_INPUT_CODES = frozenset({
    "empty_audio",
    "invalid_audio",
    "unsupported_format",
    "size_limit",
})

#: Configuration / authorization: do not retry the same route indefinitely.
PERMANENT_CONFIG_CODES = frozenset({
    "unsupported_capability",
    "authentication",
})

#: Transient provider/transport: bounded retry or route fallback is allowed.
TRANSIENT_CODES = frozenset({
    "throttling",
    "provider_failure",
})

#: Default retry disposition for each public error code.
TRANSCRIPTION_ERROR_POLICY = {
    "empty_audio": {
        "retryable": False,
        "action": "replace_chunk",
        "http_status": 422,
        "retry_after_ms": None,
    },
    "invalid_audio": {
        "retryable": False,
        "action": "replace_chunk",
        "http_status": 422,
        "retry_after_ms": None,
    },
    "unsupported_format": {
        "retryable": False,
        "action": "replace_chunk",
        "http_status": 422,
        "retry_after_ms": None,
    },
    "size_limit": {
        "retryable": False,
        "action": "replace_chunk",
        "http_status": 413,
        "retry_after_ms": None,
    },
    "authentication": {
        "retryable": False,
        "action": "configuration",
        "http_status": 403,
        "retry_after_ms": None,
    },
    "unsupported_capability": {
        "retryable": False,
        "action": "configuration",
        "http_status": 422,
        "retry_after_ms": None,
    },
    "throttling": {
        "retryable": True,
        "action": "retry",
        "http_status": 429,
        "retry_after_ms": 2000,
    },
    "provider_failure": {
        "retryable": True,
        "action": "retry",
        "http_status": 502,
        "retry_after_ms": 1000,
    },
}

_EBML_MAGIC = b"\x1a\x45\xdf\xa3"
_RIFF_MAGIC = b"RIFF"
_WAVE_MAGIC = b"WAVE"
_OGG_MAGIC = b"OggS"
_FLAC_MAGIC = b"fLaC"
_ID3_MAGIC = b"ID3"
_FTYP_MAGIC = b"ftyp"

#: Client-direct live transports. Audio never transits Odoo on these paths.
LIVE_TRANSPORTS = ("webrtc", "websocket")

#: Process-local live transcription sessions. Keyed by ``(dbname, handle)`` so
#: two databases in the same worker cannot share a provider realtime socket.
#: The browser/IoT live path uses short-lived credentials instead of this map.
_LIVE_SESSIONS = {}


class LLMTranscriptionError(UserError):
    """Normalized speech-to-text failure.

    ``name`` is the public error code. Messages must never include audio bytes,
    transcripts, credentials or PHI-bearing bodies.
    """

    def __init__(
        self,
        code,
        message,
        retryable=None,
        action=None,
        retry_after_ms=None,
        http_status=None,
    ):
        if code not in TRANSCRIPTION_ERROR_CODES:
            code = "provider_failure"
        policy = TRANSCRIPTION_ERROR_POLICY.get(
            code, TRANSCRIPTION_ERROR_POLICY["provider_failure"]
        )
        self.code = code
        self.retryable = policy["retryable"] if retryable is None else bool(retryable)
        self.action = action or policy["action"]
        self.retry_after_ms = (
            policy["retry_after_ms"] if retry_after_ms is None else retry_after_ms
        )
        self.http_status = (
            policy["http_status"] if http_status is None else int(http_status)
        )
        super().__init__(message)

    def to_dict(self, chunk_sequence=None):
        """Safe JSON contract for HTTP and telemetry. Never includes PHI."""
        return {
            "code": self.code,
            "retryable": self.retryable,
            "action": self.action,
            "message": str(self),
            "retry_after_ms": self.retry_after_ms,
            "chunk_sequence": chunk_sequence,
        }


def transcription_modes_for_use(model_use, supports_batch=False, supports_live=False):
    """Return ``{batch, live}`` without inferring live from ``model_use`` alone."""
    if model_use != "transcription":
        return {
            "batch": False,
            "live": False,
            "webrtc": False,
            "websocket": False,
            "diarization": False,
        }
    live = bool(supports_live)
    return {
        "batch": bool(supports_batch),
        "live": live,
        "webrtc": live,
        "websocket": live,
        "diarization": False,
    }


def inspect_audio_container(audio, content_type=None, filename=None):
    """Validate that ``audio`` is a complete independently decodable object.

    Returns a dict ``{format, mime, extension}``. Raises
    :class:`LLMTranscriptionError` when the bytes are empty, truncated, or do
    not match the declared MIME type / filename extension. Never logs or
    returns the audio itself.
    """
    audio_bytes = coerce_audio_bytes(audio)
    if not audio_bytes:
        raise LLMTranscriptionError(
            "empty_audio",
            _("Transcription requires audio bytes."),
        )
    detected = _detect_container(audio_bytes)
    if not detected:
        raise LLMTranscriptionError(
            "invalid_audio",
            _("Audio is missing a recognized container signature."),
        )
    declared_mime = (content_type or "").split(";", 1)[0].strip().lower()
    declared_ext = _extension_from_filename(filename)
    if declared_mime and declared_mime not in ("application/octet-stream",):
        expected = _FORMAT_BY_MIME.get(declared_mime)
        if expected and expected != detected["format"]:
            raise LLMTranscriptionError(
                "unsupported_format",
                _("Audio container does not match the declared MIME type."),
            )
    if declared_ext:
        expected = _FORMAT_BY_EXTENSION.get(declared_ext)
        if expected and expected != detected["format"]:
            raise LLMTranscriptionError(
                "unsupported_format",
                _("Audio container does not match the declared filename extension."),
            )
    return detected


def _extension_from_filename(filename):
    if not filename or "." not in filename:
        return ""
    return "." + filename.rsplit(".", 1)[-1].lower().lstrip(".")


def _detect_container(audio_bytes):
    if len(audio_bytes) < 12:
        return None
    if audio_bytes.startswith(_RIFF_MAGIC):
        if len(audio_bytes) < 44 or audio_bytes[8:12] != _WAVE_MAGIC:
            raise LLMTranscriptionError(
                "invalid_audio",
                _("WAV audio is missing a complete WAVE header."),
            )
        if b"fmt " not in audio_bytes[12:48]:
            raise LLMTranscriptionError(
                "invalid_audio",
                _("WAV audio is missing a format chunk."),
            )
        return {"format": "wav", "mime": "audio/wav", "extension": ".wav"}
    if audio_bytes.startswith(_EBML_MAGIC):
        # A standalone WebM file starts with the EBML initialization segment.
        # A MediaRecorder timeslice continuation does not, and must be rejected
        # before a paid provider is called.
        if len(audio_bytes) < 32:
            raise LLMTranscriptionError(
                "invalid_audio",
                _("WebM audio is missing initialization metadata."),
            )
        return {"format": "webm", "mime": "audio/webm", "extension": ".webm"}
    if audio_bytes.startswith(_OGG_MAGIC):
        return {"format": "ogg", "mime": "audio/ogg", "extension": ".ogg"}
    if audio_bytes.startswith(_FLAC_MAGIC):
        return {"format": "flac", "mime": "audio/flac", "extension": ".flac"}
    if audio_bytes.startswith(_ID3_MAGIC) or (
        audio_bytes[0] == 0xFF and audio_bytes[1] & 0xE0 == 0xE0
    ):
        return {"format": "mp3", "mime": "audio/mpeg", "extension": ".mp3"}
    if len(audio_bytes) >= 8 and audio_bytes[4:8] == _FTYP_MAGIC:
        return {"format": "mp4", "mime": "audio/mp4", "extension": ".m4a"}
    return None


_FORMAT_BY_MIME = {
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/flac": "flac",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mp4": "mp4",
    "audio/m4a": "mp4",
}

_FORMAT_BY_EXTENSION = {
    ".wav": "wav",
    ".webm": "webm",
    ".ogg": "ogg",
    ".flac": "flac",
    ".mp3": "mp3",
    ".mp4": "mp4",
    ".m4a": "mp4",
    ".mpeg": "mp3",
    ".mpga": "mp3",
}


def coerce_audio_bytes(audio):
    """Return audio as ``bytes``.

    Accepts ``bytes`` / ``bytearray`` / ``memoryview`` or a seekable stream.
    Strings are rejected so storage references such as ``media_key`` cannot
    reach a provider adapter.
    """
    if audio is None:
        raise LLMTranscriptionError(
            "empty_audio",
            _("Transcription requires audio bytes."),
        )
    if isinstance(audio, str):
        raise LLMTranscriptionError(
            "invalid_audio",
            _("Transcription audio must be bytes or a seekable stream."),
        )
    if isinstance(audio, memoryview):
        audio = audio.tobytes()
    if isinstance(audio, bytearray):
        audio = bytes(audio)
    if isinstance(audio, bytes):
        return audio
    read = getattr(audio, "read", None)
    if not callable(read):
        raise LLMTranscriptionError(
            "invalid_audio",
            _("Transcription audio must be bytes or a seekable stream."),
        )
    position = None
    seek = getattr(audio, "seek", None)
    tell = getattr(audio, "tell", None)
    if callable(tell):
        try:
            position = tell()
        except Exception:  # noqa: BLE001 - a closed/unseekable stream is invalid audio
            position = None
    data = read()
    if isinstance(data, str):
        raise LLMTranscriptionError(
            "invalid_audio",
            _("Transcription audio must be bytes or a seekable stream."),
        )
    if callable(seek) and position is not None:
        try:
            seek(position)
        except Exception:  # noqa: BLE001 - rewind is best-effort after the copy
            pass
    if data is None:
        return b""
    if isinstance(data, memoryview):
        return data.tobytes()
    if isinstance(data, bytearray):
        return bytes(data)
    if not isinstance(data, bytes):
        raise LLMTranscriptionError(
            "invalid_audio",
            _("Transcription audio must be bytes or a seekable stream."),
        )
    return data


def require_audio_bytes(audio):
    """Coerce audio and reject an empty payload before a provider is called."""
    audio_bytes = coerce_audio_bytes(audio)
    if not audio_bytes:
        raise LLMTranscriptionError(
            "empty_audio",
            _("Transcription requires audio bytes."),
        )
    return audio_bytes


def _normalize_alternative(item):
    if not isinstance(item, dict):
        return None
    text = item.get("text")
    if text is None:
        return None
    confidence = item.get("confidence")
    if confidence is not None:
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = None
    return {"text": str(text), "confidence": confidence}


def _normalize_segment(item):
    if not isinstance(item, dict):
        return None
    text = item.get("text")
    if text is None:
        text = ""
    start_ms = item.get("start_ms")
    end_ms = item.get("end_ms")
    try:
        start_ms = int(start_ms) if start_ms is not None else None
    except (TypeError, ValueError):
        start_ms = None
    try:
        end_ms = int(end_ms) if end_ms is not None else None
    except (TypeError, ValueError):
        end_ms = None
    confidence = item.get("confidence")
    if confidence is not None:
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = None
    speaker = item.get("speaker")
    if speaker is not None:
        speaker = str(speaker)
    alternatives = []
    for alternative in item.get("alternatives") or []:
        normalized = _normalize_alternative(alternative)
        if normalized:
            alternatives.append(normalized)
    return {
        "text": str(text),
        "start_ms": start_ms,
        "end_ms": end_ms,
        "speaker": speaker,
        "confidence": confidence,
        "alternatives": alternatives,
    }


def normalize_transcription_result(result):
    """Keep only the public STT contract and drop provider-native objects."""
    if not isinstance(result, dict):
        raise LLMTranscriptionError(
            "provider_failure",
            _("Transcription result is not a normalized dictionary."),
        )
    text = result.get("text")
    if text is None:
        text = ""
    duration_ms = result.get("duration_ms")
    try:
        duration_ms = int(duration_ms) if duration_ms is not None else None
    except (TypeError, ValueError):
        duration_ms = None
    language = result.get("language")
    if language is not None:
        language = str(language)
    segments = []
    for item in result.get("segments") or []:
        segment = _normalize_segment(item)
        if segment is not None:
            segments.append(segment)
    if not segments and text:
        segments.append(
            {
                "text": str(text),
                "start_ms": 0,
                "end_ms": duration_ms,
                "speaker": None,
                "confidence": None,
                "alternatives": [],
            }
        )
    provider_request_id = result.get("provider_request_id")
    if provider_request_id is not None:
        provider_request_id = str(provider_request_id)
    model_version = result.get("model_version")
    if model_version is not None:
        model_version = str(model_version)
    return {
        "text": str(text),
        "language": language,
        "duration_ms": duration_ms,
        "segments": segments,
        "provider_request_id": provider_request_id,
        "model_version": model_version,
    }


def normalize_live_credentials(payload):
    """Keep only the public short-lived live-STT credential contract.

    The payload must never include a long-lived provider API key. Callers send
    ``token`` to the provider realtime endpoint and nothing else.
    """
    if not isinstance(payload, dict):
        raise LLMTranscriptionError(
            "provider_failure",
            _("Live transcription credentials are not a normalized dictionary."),
        )
    for forbidden in ("api_key", "apiKey", "secret_key", "openai_api_key"):
        if payload.get(forbidden):
            raise LLMTranscriptionError(
                "authentication",
                _("Live transcription credentials must not include a long-lived secret."),
            )
    transport = payload.get("transport")
    if transport not in LIVE_TRANSPORTS:
        raise LLMTranscriptionError(
            "unsupported_capability",
            _("Live transcription requires a webrtc or websocket transport."),
        )
    token = payload.get("token") or (payload.get("client_secret") or {}).get("value")
    if not token:
        raise LLMTranscriptionError(
            "authentication",
            _("Live transcription credentials are missing a short-lived token."),
        )
    session_id = payload.get("session_id") or payload.get("handle") or ""
    expires_at = payload.get("expires_at")
    if expires_at is not None:
        expires_at = str(expires_at)
    return {
        "token": str(token),
        "expires_at": expires_at,
        "url": str(payload.get("url") or ""),
        "transport": transport,
        "session_id": str(session_id),
        "handle": str(payload.get("handle") or session_id),
        "ice_servers": payload.get("ice_servers") or [],
        "input_audio_format": str(payload.get("input_audio_format") or "pcm16"),
        "model": str(payload.get("model") or "") or None,
    }


def normalize_live_events(events):
    """Normalize live transcription events to the public transcript schema."""
    normalized = []
    for item in events or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind") or item.get("type") or "partial"
        if kind not in ("partial", "final"):
            kind = "partial"
        segment = _normalize_segment(item) or {
            "text": str(item.get("text") or ""),
            "start_ms": item.get("start_ms"),
            "end_ms": item.get("end_ms"),
            "speaker": item.get("speaker"),
            "confidence": item.get("confidence"),
            "alternatives": [],
        }
        normalized.append({
            "kind": kind,
            "replaceable": kind == "partial",
            **segment,
        })
    return normalized


def live_session_key(dbname, handle):
    return (dbname, handle)


def create_live_session(dbname, payload):
    """Store a provider-neutral live-session handle in this worker."""
    handle = payload.get("handle") or uuid.uuid4().hex
    record = dict(payload, handle=handle, closed=False, buffer=bytearray())
    _LIVE_SESSIONS[live_session_key(dbname, handle)] = record
    return handle


def get_live_session(dbname, handle):
    record = _LIVE_SESSIONS.get(live_session_key(dbname, handle))
    if not record or record.get("closed"):
        raise LLMTranscriptionError(
            "invalid_audio",
            _("The live transcription session is closed or unknown."),
        )
    return record


def close_live_session(dbname, handle):
    record = _LIVE_SESSIONS.pop(live_session_key(dbname, handle), None)
    if record:
        record["closed"] = True
    return record
