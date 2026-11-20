"""Provider-neutral media capability contract for LLM chat.

The coarse ``model_use = multimodal`` flag is not enough to gate image, PDF,
text-file, batch STT, direct audio chat and realtime voice independently.
Call ``llm.model.get_media_capabilities()`` from the composer UI, thread
validation and provider formatters.
"""

from dataclasses import asdict, dataclass
from typing import Optional

# Attachment description markers (ir.attachment.description).
# Dictation audio is kept for playback/audit but must never be sent as
# provider audio input. Analyze audio is only included when the model
# explicitly supports direct audio chat.
LLM_MEDIA_ROLE_DICTATION = "llm_media_role=dictation"
LLM_MEDIA_ROLE_ANALYZE = "llm_media_role=analyze"

# Soft defaults; providers may tighten these via media_capabilities().
DEFAULT_MAX_IMAGE_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_PDF_BYTES = 32 * 1024 * 1024
DEFAULT_MAX_TEXT_BYTES = 1 * 1024 * 1024
DEFAULT_MAX_AUDIO_BYTES = 25 * 1024 * 1024


@dataclass(frozen=True)
class MediaCapabilities:
    """What the selected chat model/endpoint accepts as user input."""

    image: bool = False
    pdf: bool = False
    text_file: bool = True
    # Direct audio input to the *chat* model (e.g. OpenAI input_audio).
    # Distinct from batch STT used for dictation.
    direct_audio_chat: bool = False
    # Live duplex voice (MH-1330). Always False here until that addon gates it.
    realtime_voice: bool = False
    # Provider-preferred chat endpoint when applicable (openai only).
    # ``responses`` | ``chat_completions`` | None
    chat_endpoint: Optional[str] = None
    # Whether function tools can be used together with direct audio input.
    tools_with_direct_audio: bool = False
    max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES
    max_pdf_bytes: int = DEFAULT_MAX_PDF_BYTES
    max_text_bytes: int = DEFAULT_MAX_TEXT_BYTES
    max_audio_bytes: int = DEFAULT_MAX_AUDIO_BYTES

    def to_dict(self):
        return asdict(self)


def attachment_media_role(attachment):
    """Return ``dictation``, ``analyze`` or None from attachment description."""
    description = (getattr(attachment, "description", None) or "").lower()
    if LLM_MEDIA_ROLE_DICTATION in description:
        return "dictation"
    if LLM_MEDIA_ROLE_ANALYZE in description:
        return "analyze"
    return None


def mark_attachment_media_role(description, role):
    """Return an updated description string with the media role marker."""
    base = (description or "").strip()
    # Drop any previous role marker so roles are mutually exclusive.
    parts = [
        part
        for part in base.splitlines()
        if LLM_MEDIA_ROLE_DICTATION not in part.lower()
        and LLM_MEDIA_ROLE_ANALYZE not in part.lower()
    ]
    marker = (
        LLM_MEDIA_ROLE_DICTATION if role == "dictation" else LLM_MEDIA_ROLE_ANALYZE
    )
    parts.append(marker)
    return "\n".join(part for part in parts if part).strip()
