"""Provider-neutral speech-to-text contract.

Application code calls ``llm.model.transcribe()``. Provider modules implement
``<service>_transcribe()`` and return a normalized dictionary. Storage keys,
signed URLs and other application references must never enter this layer.
"""

from odoo import _
from odoo.exceptions import UserError

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


class LLMTranscriptionError(UserError):
    """Normalized speech-to-text failure.

    ``name`` is the public error code. Messages must never include audio bytes,
    transcripts, credentials or PHI-bearing bodies.
    """

    def __init__(self, code, message):
        if code not in TRANSCRIPTION_ERROR_CODES:
            code = "provider_failure"
        self.code = code
        super().__init__(message)


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
