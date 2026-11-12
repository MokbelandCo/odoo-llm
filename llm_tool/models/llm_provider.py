import json
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class LLMProvider(models.Model):
    _inherit = "llm.provider"

    @api.model
    def _is_tool_call_complete(self, function_data, expected_endings=("]", "}")):
        """Check if a tool call is complete (utility function for providers).

        Args:
            function_data: Dictionary with 'name' and 'arguments' keys
            expected_endings: Tuple of valid JSON ending characters

        Returns:
            bool: True if the tool call appears complete
        """
        tool_name = function_data.get("name")
        args_str = function_data.get("arguments", "").strip()

        if not tool_name or not args_str:
            return False

        try:
            json.loads(args_str)
            if args_str.endswith(expected_endings):
                return True
        except json.JSONDecodeError:
            pass

        return False

    def _prepare_prepend_messages(self, prepend_messages, tools):
        """Inject tool consent instructions into prepend messages.

        Overrides the base hook to add consent instructions for tools
        that require user consent before execution.

        Args:
            prepend_messages: List of pre-formatted message dicts
            tools: llm.tool recordset of available tools

        Returns:
            List of message dicts with consent instructions injected
        """
        prepend_messages = super()._prepare_prepend_messages(prepend_messages, tools)

        if not tools:
            return prepend_messages

        consent_required = tools.filtered(lambda t: t.requires_user_consent)
        if not consent_required:
            return prepend_messages

        return self._inject_tool_consent(prepend_messages, consent_required)

    def _inject_tool_consent(self, prepend_messages, consent_tools):
        """Add consent instructions to prepend messages.

        Args:
            prepend_messages: List of message dicts to modify
            consent_tools: llm.tool recordset of tools requiring consent

        Returns:
            List of message dicts with consent instructions added
        """
        tool_names = ", ".join([f"'{t.name}'" for t in consent_tools])
        config = self.env["llm.tool.consent.config"].get_active_config()
        consent_instruction = config.system_message_template.format(
            tool_names=tool_names
        )
        return self._append_system_instruction(prepend_messages, consent_instruction)

    def _append_system_instruction(self, prepend_messages, instruction):
        """Append ``instruction`` to the first system message.

        Inserts a system message when there is none. The instruction is added
        only once, so calling this again with the same text is a no-op.

        Args:
            prepend_messages: List of message dicts
            instruction: Plain text to append

        Returns:
            New list of message dicts
        """
        # Make a copy to avoid modifying the original
        prepend_messages = list(prepend_messages or [])
        if not instruction:
            return prepend_messages

        for index, msg in enumerate(prepend_messages):
            if msg.get("role") == "system":
                content = msg.get("content", "")
                existing_text = self._extract_content_text(content)
                if instruction in existing_text:
                    return prepend_messages
                separator = "\n\n" if existing_text else ""
                text = f"{existing_text}{separator}{instruction}"

                # Keep the list format, replacing the first text part
                if (
                    isinstance(content, list)
                    and content
                    and isinstance(content[0], dict)
                ):
                    content = [{**content[0], "text": text}, *content[1:]]
                else:
                    content = text
                prepend_messages[index] = {**msg, "content": content}

                return prepend_messages

        # No system message found, insert one at the beginning
        prepend_messages.insert(
            0,
            {
                "role": "system",
                "content": instruction,
            },
        )

        return prepend_messages
