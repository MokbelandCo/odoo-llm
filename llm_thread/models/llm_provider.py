from odoo import models

from .. import record_links

RECORD_LINK_INSTRUCTION = (
    "Odoo record links: a tool result may contain a record reference with a "
    '"link_token" such as [[odoo-ref:AbCdEf123456]]. When your reply mentions '
    "that record, copy its link_token exactly where the link should appear. "
    "You may add link text after a vertical bar, for example "
    "[[odoo-ref:AbCdEf123456|Click here]]. Only use link_token values that "
    "appear in tool results of this conversation; never invent, guess or "
    "change them. Never write HTML, JavaScript or URLs to link Odoo records, "
    "and never put link tokens inside code blocks or inline code; the chat "
    "turns the token into the link."
)


class LLMProvider(models.Model):
    _inherit = "llm.provider"

    def _prepare_prepend_messages(self, prepend_messages, tools):
        """Add the record-link instruction for built-in Odoo chat requests.

        Only requests started by the chat UI endpoint carry
        ``record_links.CONTEXT_KEY``; other callers of ``chat()`` (MCP,
        automation, prompt tests) never get this instruction.
        """
        prepend_messages = super()._prepare_prepend_messages(prepend_messages, tools)
        if not tools or not self.env.context.get(record_links.CONTEXT_KEY):
            return prepend_messages
        return self._append_system_instruction(
            prepend_messages, self._get_record_link_instruction()
        )

    def _get_record_link_instruction(self):
        return RECORD_LINK_INSTRUCTION
