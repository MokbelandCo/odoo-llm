import copy
import logging

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, MissingError, UserError, ValidationError
from odoo.osv import expression

from odoo.addons.llm_tool.record_reference import iter_record_references

from .. import record_links

_logger = logging.getLogger(__name__)


class MailMessage(models.Model):
    _inherit = "mail.message"

    user_vote = fields.Integer(
        string="User Vote",
        default=0,
        help="Vote status given by the user. 0: No vote, 1: Upvoted, -1: Downvoted.",
    )

    @api.model
    def _message_fetch(
        self, domain, search_term=None, before=None, after=None, around=None, limit=30
    ):
        """Override to filter only LLM messages for llm.thread model.

        For LLM threads, we only want to display messages that have an llm_role
        (user, assistant, tool), not system notifications or other message types
        that would clutter the AI conversation interface.
        """
        # Check if this is for an llm.thread model
        is_llm_thread = any(
            condition[0] == "model"
            and condition[1] == "="
            and condition[2] == "llm.thread"
            for condition in domain
            if isinstance(condition, (list, tuple)) and len(condition) == 3
        )

        if is_llm_thread:
            # Add LLM role filter for LLM threads - only show messages with llm_role
            llm_role_filter = [("llm_role", "!=", False)]
            domain = expression.AND([domain, llm_role_filter])

        # Call parent method with modified domain
        return super()._message_fetch(domain, search_term, before, after, around, limit)

    def _get_message_format_fields(self):
        fields_list = super()._get_message_format_fields()
        if "user_vote" not in fields_list:
            fields_list.append("user_vote")
        return fields_list

    def message_format(self, format_reply=True, msg_vals=None):
        vals_list = super().message_format(
            format_reply=format_reply, msg_vals=msg_vals
        )
        messages_by_id = {message.id: message for message in self}
        for vals in vals_list:
            message = messages_by_id.get(vals.get("id"))
            if message:
                vals["user_vote"] = message.user_vote
        self._llm_format_record_links(vals_list, messages_by_id)
        return vals_list

    # ------------------------------------------------------------------
    # Record links (see llm_thread/docs/RECORD_LINKS.md)
    # ------------------------------------------------------------------

    def _postprocess_tool_data(self, tool_data, thread_model=None):
        """Issue thread-bound references for record references in the result."""
        tool_data = super()._postprocess_tool_data(tool_data, thread_model)
        thread = thread_model
        if thread is None and self.model == "llm.thread" and self.res_id:
            thread = self.env["llm.thread"].browse(self.res_id)
        if not thread or thread._name != "llm.thread":
            return tool_data
        result = tool_data.get("result")
        if not any(iter_record_references(result)):
            return tool_data
        result = copy.deepcopy(result)
        entries = record_links.issue_record_refs(self.env, thread.id, result)
        return {**tool_data, "result": result, "record_refs": entries}

    def _llm_format_record_links(self, vals_list, messages_by_id):
        """Render record tokens and fallback links for the current viewer.

        Stored bodies keep the raw tokens; links are resolved on every read
        against the issuing thread's tool messages and the viewer's current
        access, so deleted or revoked records never render a label or link.
        """
        resolvers = {}
        for vals in vals_list:
            message = messages_by_id.get(vals.get("id"))
            if not message or message.model != "llm.thread" or not message.res_id:
                continue
            if message.llm_role == "tool":
                body_json = vals.get("body_json")
                if isinstance(body_json, dict) and "record_refs" in body_json:
                    vals["body_json"] = {
                        key: value
                        for key, value in body_json.items()
                        if key != "record_refs"
                    }
                    resolver = resolvers.setdefault(
                        message.res_id, self._llm_record_ref_resolver(message.res_id)
                    )
                    vals["llm_record_links"] = self._llm_record_links_payload(
                        body_json["record_refs"], resolver
                    )
            elif message.llm_role == "assistant":
                body = vals.get("body")
                if not body or record_links.TOKEN_MARKER not in body:
                    continue
                resolver = resolvers.setdefault(
                    message.res_id, self._llm_record_ref_resolver(message.res_id)
                )
                vals["body"] = Markup(
                    record_links.render_record_tokens(
                        body,
                        resolver,
                        unavailable_text=_("record link unavailable"),
                        title_format=_("Open %s"),
                    )
                )

    @api.model
    def _llm_record_links_payload(self, entries, resolver):
        links = []
        seen = set()
        for entry in entries or []:
            ref_id = entry.get("ref") if isinstance(entry, dict) else None
            resolved = resolver(ref_id) if ref_id else None
            if not resolved:
                continue
            key = (resolved["model"], resolved["res_id"])
            if key in seen:
                continue
            seen.add(key)
            links.append(record_links.link_payload(resolved))
        return links

    @api.model
    def _llm_record_ref_resolver(self, thread_id):
        """Return ``ref_id -> entry | None`` for one thread and the current user."""
        thread = self.env["llm.thread"].browse(thread_id)
        try:
            thread.check_access_rights("read")
            thread.check_access_rule("read")
        except (AccessError, MissingError):
            return lambda ref_id: None

        entries = {}
        tool_messages = self.sudo().search(
            [
                ("model", "=", "llm.thread"),
                ("res_id", "=", thread_id),
                ("llm_role", "=", "tool"),
            ]
        )
        for tool_message in tool_messages:
            body_json = tool_message.body_json
            if not isinstance(body_json, dict):
                continue
            for entry in body_json.get("record_refs") or []:
                if record_links.is_valid_entry(self.env, thread_id, entry):
                    entries.setdefault(entry["ref"], entry)

        cache = {}

        def resolve(ref_id):
            if ref_id not in cache:
                entry = entries.get(ref_id)
                readable = entry and record_links.get_readable_record(
                    self.env, entry["model"], entry["res_id"]
                )
                cache[ref_id] = entry if readable else None
            return cache[ref_id]

        return resolve

    def set_user_vote(self, vote_value):
        """Sets the user vote on this message, performing validation checks."""
        self.ensure_one()
        if self.llm_role not in ("assistant", "tool"):
            raise UserError(
                _(
                    "Voting is only allowed on messages generated by the assistant or a tool."
                )
            )
        if not isinstance(vote_value, int) or vote_value not in [-1, 0, 1]:
            raise ValidationError(
                _(
                    "Invalid vote value. Must be an integer: 1 (up), -1 (down), or 0 (none)."
                )
            )
        self.sudo().write({"user_vote": vote_value})
