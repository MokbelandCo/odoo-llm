import json
import logging

from odoo import models, tools

from ..utils.openai_responses import REPLAY_KEY, STATE_KEY

_logger = logging.getLogger(__name__)


class MailMessage(models.Model):
    _inherit = "mail.message"

    def openai_format_message(self, is_multimodal=False, is_audio_model=False):
        """Format message for OpenAI API.

        Args:
            is_multimodal: Whether the model supports images/PDFs
            is_audio_model: Whether the model supports audio (gpt-4o-audio-preview)
        """
        self.ensure_one()
        body = self.body
        if body:
            body = tools.html2plaintext(body)

        if self.is_llm_user_message()[self]:
            texts = self._get_text_attachments()

            # Only include images/PDFs if model supports multimodal
            if is_multimodal:
                images = self._get_image_attachments()
                pdfs = self._get_pdf_attachments()
            else:
                images = []
                pdfs = []

            # Only include analyze-mode audio when the model/endpoint supports it.
            # Dictation-source recordings stay attached for audit but are never
            # sent as input_audio.
            if is_audio_model:
                audios = [
                    audio
                    for audio in self._get_audio_attachments()
                    if audio.get("media_role") == "analyze"
                ]
            else:
                audios = []

            has_attachments = images or pdfs or texts or audios

            if has_attachments:
                content = []

                text_parts = []
                if body and body.strip():
                    text_parts.append(body.strip())

                for txt in texts:
                    text_parts.append(f"--- {txt['name']} ---\n{txt['content']}")

                if text_parts:
                    content.append({"type": "text", "text": "\n\n".join(text_parts)})
                elif images or pdfs or audios:
                    content.append(
                        {"type": "text", "text": "Please analyze these files."},
                    )

                for img in images:
                    content.append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{img['mimetype']};base64,{img['data']}",
                            },
                        },
                    )

                for pdf in pdfs:
                    content.append(
                        {
                            "type": "file",
                            "file": {
                                "filename": pdf["name"],
                                "file_data": f"data:{pdf['mimetype']};base64,{pdf['data']}",
                            },
                        },
                    )

                for audio in audios:
                    content.append(
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": audio["data"],
                                "format": audio["format"],
                            },
                        },
                    )

                return {"role": "user", "content": content}

            if not body or not body.strip():
                return None
            return {"role": "user", "content": body}

        if self.is_llm_assistant_message()[self]:
            formatted_message = {"role": "assistant"}

            formatted_message["content"] = body

            # Add tool calls if present in body_json
            tool_calls = self.get_tool_calls()
            if tool_calls:
                formatted_message["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": tc.get("type", "function"),
                        "function": {
                            "name": tc["function"]["name"],
                            "arguments": tc["function"]["arguments"],
                        },
                    }
                    for tc in tool_calls
                ]

            state = self._openai_replay_state()
            if state:
                formatted_message[REPLAY_KEY] = state

            return formatted_message

        if self.is_llm_tool_message()[self]:
            tool_data = self.body_json
            if not tool_data:
                _logger.warning(
                    f"OpenAI Format: Skipping tool message {self.id}: no tool data found.",
                )
                return None

            tool_call_id = tool_data.get("tool_call_id")
            if not tool_call_id:
                _logger.warning(
                    f"OpenAI Format: Skipping tool message {self.id}: missing tool_call_id.",
                )
                return None

            # Get result content
            if "result" in tool_data:
                content = json.dumps(tool_data["result"])
            elif "error" in tool_data:
                content = json.dumps({"error": tool_data["error"]})
            else:
                content = ""

            formatted_message = {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": content,
            }
            return formatted_message
        return None

    def _openai_replay_state(self):
        """Stored Responses output for this assistant turn, for manual replay.

        ``text_matches_body`` is False once the visible body was edited, so
        the encrypted reasoning that produced the old text is not replayed.
        """
        self.ensure_one()
        state = ((self.sudo().llm_provider_state or {}).get(STATE_KEY)) or {}
        if not state:
            return None
        stored_text = state.get("text") or ""
        current = tools.html2plaintext(self.body or "").strip()
        if "llm.thread" in self.env:
            rendered = self.env["llm.thread"]._process_llm_body(stored_text) if stored_text else ""
            expected = tools.html2plaintext(rendered or "").strip()
        else:
            expected = stored_text.strip()
        return dict(state, text_matches_body=current == expected)
