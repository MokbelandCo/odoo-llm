import json
import logging
import re

from odoo import api, models
from odoo.tools import html2plaintext

from odoo.addons.llm_thread.models.llm_provider import RECORD_LINK_INSTRUCTION

_logger = logging.getLogger(__name__)

_DUMMY_REPLY = "Dummy reply: the AI popup chat is working."
_DEMO_TOOL = "llm_dummy_find_contact"
# "record link", "record link for Azure", "record link without link for Azure"
_RECORD_LINK_REQUEST = re.compile(
    r"record link(?: without link)?(?: for (.+))?$", re.IGNORECASE
)


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
        last_message = messages[-1:] if messages else self.env["mail.message"]
        last_text = self._dummy_message_text(last_message) if last_message else ""
        reply = _DUMMY_REPLY
        if last_text:
            reply = f"Dummy reply: {last_text}"
        if last_message.llm_role == "tool":
            reply = self._dummy_record_link_reply(messages, prepend_messages)
        elif tools and _DEMO_TOOL in tools.mapped("name"):
            match = _RECORD_LINK_REQUEST.search(last_text)
            if match:
                tool_call = {
                    "id": f"call_dummy_{last_message.id}",
                    "type": "function",
                    "function": {
                        "name": _DEMO_TOOL,
                        "arguments": json.dumps({"name": (match.group(1) or "").strip()}),
                    },
                }
                yield {"tool_calls": [tool_call]}
                return
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

    def _dummy_record_link_reply(self, messages, prepend_messages):
        """Reply to the demo tool result like a model following the chat's
        record-link instruction: copy the issued token only when the shared
        instruction is present and the user did not ask to omit it."""
        result = (messages[-1].body_json or {}).get("result")
        contact = result.get("contact") if isinstance(result, dict) else None
        if not contact:
            return "Dummy reply: no matching contact."
        user_messages = messages.filtered(lambda m: m.llm_role == "user")
        user_text = self._dummy_message_text(user_messages[-1:]).lower()
        system_text = "\n".join(
            self._extract_content_text(msg.get("content"))
            for msg in prepend_messages or []
            if msg.get("role") == "system"
        )
        token = contact.get("link_token") or ""
        if (
            token.startswith("[[odoo-ref:")
            and RECORD_LINK_INSTRUCTION in system_text
            and "without link" not in user_text
        ):
            return f"Contact {contact['label']} found. {token[:-2]}|Click here]] to open it."
        return f"Contact {contact['label']} found."

    @staticmethod
    def _dummy_message_text(message):
        body = getattr(message, "body", "") or ""
        if not isinstance(body, str):
            body = str(body)
        return html2plaintext(body).strip()
