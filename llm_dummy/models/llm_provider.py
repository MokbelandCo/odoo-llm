import logging

from odoo import api, models
from odoo.tools import html2plaintext

_logger = logging.getLogger(__name__)

_DUMMY_REPLY = "Dummy reply: the AI popup chat is working."


class LLMProvider(models.Model):
    _inherit = "llm.provider"

    @api.model
    def _get_available_services(self):
        return super()._get_available_services() + [("dummy", "Dummy")]

    def dummy_get_client(self):
        return None

    def dummy_normalize_prepend_messages(self, prepend_messages):
        return prepend_messages or []

    def dummy_format_tools(self, tools):
        return []

    def dummy_format_messages(self, messages, system_prompt=None, model=None):
        formatted = []
        if system_prompt:
            formatted.append({"role": "system", "content": system_prompt})
        for message in messages:
            formatted.append(
                {
                    "role": message.llm_role or "user",
                    "content": self._dummy_message_text(message),
                }
            )
        return formatted

    def dummy_chat(
        self,
        messages,
        model=None,
        stream=False,
        tools=None,
        prepend_messages=None,
        **kwargs,
    ):
        last_text = ""
        if messages:
            last_text = self._dummy_message_text(messages[-1])
        reply = _DUMMY_REPLY
        if last_text:
            reply = f"Dummy reply: {last_text}"
        if stream:
            for index in range(0, len(reply), 16):
                yield {"content": reply[index : index + 16]}
            return
        yield {"role": "assistant", "content": reply}

    def dummy_models(self, model_id=None):
        yield {
            "name": "dummy-chat",
            "details": {
                "id": "dummy-chat",
                "capabilities": ["chat"],
            },
        }

    @staticmethod
    def _dummy_message_text(message):
        body = getattr(message, "body", "") or ""
        if not isinstance(body, str):
            body = str(body)
        return html2plaintext(body).strip()
