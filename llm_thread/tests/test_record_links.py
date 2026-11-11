import json
import re
from unittest.mock import patch

from lxml import html as lxml_html
from markupsafe import Markup

from odoo.tests.common import TransactionCase, new_test_user, tagged
from odoo.tools import html2plaintext

from odoo.addons.llm_thread import record_links
from odoo.addons.llm_thread.models.llm_provider import RECORD_LINK_INSTRUCTION
from odoo.addons.llm_tool.record_reference import record_reference

TOOL_NAME = "test_record_link_tool"


@tagged("post_install", "-at_install")
class TestRecordLinks(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        services = cls.env["llm.provider"]._selection_service()
        cls.provider = cls.env["llm.provider"].create(
            {
                "name": "Record Link Test Provider",
                "service": services[0][0] if services else "dummy",
            }
        )
        cls.model = cls.env["llm.model"].create(
            {
                "name": "record-link-test-model",
                "provider_id": cls.provider.id,
                "model_use": "chat",
            }
        )
        cls.tool = cls.env["llm.tool"].create(
            {
                "name": TOOL_NAME,
                "description": "Returns record references (test)",
                "implementation": "function",
            }
        )
        cls.user = new_test_user(
            cls.env, login="llm_record_link_user", groups="base.group_user"
        )
        cls.other_user = new_test_user(
            cls.env, login="llm_record_link_other", groups="base.group_user"
        )
        cls.other_company = cls.env["res.company"].create({"name": "Other Co"})
        cls.partner = cls.env["res.partner"].create({"name": "Linked Partner"})

    def setUp(self):
        super().setUp()
        self.thread = self._new_thread(self.user)

    # Helpers --------------------------------------------------------------

    def _new_thread(self, user):
        return (
            self.env["llm.thread"]
            .with_user(user)
            .create(
                {
                    "name": "Record link thread",
                    "provider_id": self.provider.id,
                    "model_id": self.model.id,
                    "user_id": user.id,
                    "tool_ids": [(6, 0, self.tool.ids)],
                }
            )
        )

    def _run_tool(self, thread, result):
        """Run the test tool on ``thread`` returning ``result``; return the message."""
        tool_call = {
            "id": "call_test",
            "type": "function",
            "function": {"name": TOOL_NAME, "arguments": "{}"},
        }
        message = self.env["mail.message"].with_user(thread.env.user)
        tool_message = message.post_tool_call(tool_call, thread_model=thread)
        tool_cls = type(self.env["llm.tool"])
        with patch.object(tool_cls, "execute", autospec=True, return_value=result):
            list(tool_message.execute_tool_call(thread_model=thread))
        return tool_message

    def _issue(self, thread=None, record=None):
        thread = thread or self.thread
        record = record or self.partner
        tool_message = self._run_tool(thread, {"contact": record_reference(record)})
        token = tool_message.body_json["result"]["contact"]["link_token"]
        return tool_message, token

    def _assistant(self, body, thread=None):
        """Post an assistant reply the way streaming stores it."""
        thread = thread or self.thread
        message = thread.message_post(body="…", llm_role="assistant", author_id=False)
        message.write({"body": thread._process_llm_body(body)})
        return message

    def _formatted(self, message, user=None):
        return message.with_user(user or self.user).message_format()[0]

    def _ref(self, token):
        return token[len(record_links.TOKEN_MARKER) : -2]

    # Issuance -------------------------------------------------------------

    def test_tool_result_issues_signed_ref(self):
        other = self.env["res.partner"].create({"name": "Second"})
        tool_message = self._run_tool(
            self.thread,
            {
                "contact": record_reference(self.partner),
                "again": record_reference(self.partner),
                "other": record_reference(other),
            },
        )
        body_json = tool_message.body_json
        result = body_json["result"]
        self.assertRegex(
            result["contact"]["link_token"], r"^\[\[odoo-ref:[A-Za-z0-9]{12}\]\]$"
        )
        self.assertEqual(result["contact"]["link_token"], result["again"]["link_token"])
        self.assertNotEqual(
            result["contact"]["link_token"], result["other"]["link_token"]
        )
        self.assertEqual(len(body_json["record_refs"]), 2)
        for entry in body_json["record_refs"]:
            self.assertTrue(
                record_links.is_valid_entry(self.env, self.thread.id, entry)
            )

    def test_unreadable_reference_is_not_issued(self):
        hidden = self.env["res.partner"].create(
            {"name": "Hidden", "company_id": self.other_company.id}
        )
        tool_message = self._run_tool(self.thread, {"contact": record_reference(hidden)})
        self.assertIsNone(tool_message.body_json["result"]["contact"]["link_token"])
        self.assertEqual(tool_message.body_json["record_refs"], [])

    def test_result_without_reference_is_unchanged(self):
        tool_message = self._run_tool(self.thread, {"count": 3})
        self.assertEqual(tool_message.body_json["result"], {"count": 3})
        self.assertNotIn("record_refs", tool_message.body_json)

    def test_history_keeps_token_for_the_model(self):
        """The model sees the token in the tool result and in its own replies."""
        tool_message, token = self._issue()
        self.assertIn(token, json.dumps(tool_message.body_json["result"]))
        message = self._assistant(f"See {token}")
        self.assertIn(token, message.body)
        self.assertIn(token, html2plaintext(message.body))

    # Rendering ------------------------------------------------------------

    def test_inline_token_renders_native_anchor(self):
        _tool_message, token = self._issue()
        ref = self._ref(token)
        message = self._assistant(f"Found it. [[odoo-ref:{ref}|Click here]] to open.")
        body = self._formatted(message)["body"]
        self.assertIsInstance(body, Markup)
        anchor = re.search(r"<a [^>]*>[^<]*</a>", body).group(0)
        self.assertIn('class="o_llm_record_link"', anchor)
        self.assertIn('data-oe-model="res.partner"', anchor)
        self.assertIn(f'data-oe-id="{self.partner.id}"', anchor)
        self.assertIn(
            f'href="/web#model=res.partner&amp;id={self.partner.id}&amp;view_type=form"',
            anchor,
        )
        self.assertIn('title="Open Linked Partner"', anchor)
        self.assertIn(">Click here</a>", anchor)
        self.assertNotIn("odoo-ref", body)

    def test_token_without_text_uses_label(self):
        _tool_message, token = self._issue()
        body = self._formatted(self._assistant(f"Open {token}"))["body"]
        self.assertIn(">Linked Partner</a>", body)

    def test_label_and_link_text_are_escaped(self):
        evil = self.env["res.partner"].create(
            {"name": '<img src=x onerror="alert(1)">'}
        )
        _tool_message, token = self._issue(record=evil)
        ref = self._ref(token)
        message = self._assistant(f"{token} [[odoo-ref:{ref}|<b onclick=x>t</b>]]")
        body = self._formatted(message)["body"]
        root = lxml_html.fragment_fromstring(str(body), create_parent="div")
        self.assertFalse(root.xpath("//img | //script"))
        self.assertFalse(
            [n for n in root.iter() for a in n.attrib if a.lower().startswith("on")]
        )
        anchors = root.xpath("//a[@class='o_llm_record_link']")
        self.assertTrue(anchors)
        self.assertEqual(anchors[0].text, evil.name)

    def test_tokens_in_code_stay_literal(self):
        _tool_message, token = self._issue()
        message = self._assistant(
            Markup("<p><code>{0}</code></p><pre><code>{0}</code></pre>").format(token)
        )
        body = self._formatted(message)["body"]
        self.assertNotIn("<a ", body)
        self.assertEqual(body.count(token), 2)

    def test_unknown_and_malformed_tokens_are_inert(self):
        self._issue()
        message = self._assistant(
            "[[odoo-ref:ZZZZZZZZZZZZ]] [[odoo-ref:bad id|Click]] [[odoo-ref:]]"
        )
        body = self._formatted(message)["body"]
        self.assertNotIn("<a ", body)
        self.assertNotIn("Linked Partner", body)
        self.assertEqual(body.count(record_links.UNAVAILABLE_CLASS), 3)

    def test_forged_entry_does_not_resolve(self):
        """An RPC-written record_refs entry without a valid signature is ignored."""
        tool_message, token = self._issue()
        body_json = dict(tool_message.body_json)
        forged = dict(body_json["record_refs"][0], res_id=self.env.user.partner_id.id)
        body_json["record_refs"] = [forged]
        tool_message.sudo().write({"body_json": body_json})
        body = self._formatted(self._assistant(token))["body"]
        self.assertNotIn("<a ", body)
        self.assertIn(record_links.UNAVAILABLE_CLASS, body)
        self.assertEqual(self._formatted(tool_message)["llm_record_links"], [])

    def test_ref_from_another_thread_does_not_resolve(self):
        _tool_message, token = self._issue()
        other_thread = self._new_thread(self.user)
        body = self._formatted(self._assistant(token, thread=other_thread))["body"]
        self.assertNotIn("<a ", body)
        self.assertNotIn("Linked Partner", body)

    def test_copied_signed_entry_does_not_resolve_in_another_thread(self):
        tool_message, token = self._issue()
        other_thread = self._new_thread(self.user)
        copy_message = self._run_tool(other_thread, {"count": 1})
        copy_message.sudo().write(
            {
                "body_json": dict(
                    copy_message.body_json,
                    record_refs=tool_message.body_json["record_refs"],
                )
            }
        )
        body = self._formatted(self._assistant(token, thread=other_thread))["body"]
        self.assertNotIn("<a ", body)

    def test_deleted_record_does_not_render(self):
        partner = self.env["res.partner"].create({"name": "Soon Gone"})
        tool_message, token = self._issue(record=partner)
        partner.unlink()
        body = self._formatted(self._assistant(token))["body"]
        self.assertNotIn("<a ", body)
        self.assertNotIn("Soon Gone", body)
        self.assertEqual(self._formatted(tool_message)["llm_record_links"], [])

    def test_revoked_access_does_not_render(self):
        partner = self.env["res.partner"].create({"name": "Moved Away"})
        tool_message, token = self._issue(record=partner)
        partner.company_id = self.other_company
        message = self._assistant(token)
        body = self._formatted(message)["body"]
        self.assertNotIn("<a ", body)
        self.assertNotIn("Moved Away", body)
        self.assertEqual(self._formatted(tool_message)["llm_record_links"], [])

    def test_viewer_without_thread_access_gets_no_links(self):
        _tool_message, token = self._issue()
        resolver = (
            self.env["mail.message"]
            .with_user(self.other_user)
            ._llm_record_ref_resolver(self.thread.id)
        )
        self.assertIsNone(resolver(self._ref(token)))

    def test_markdown_links_are_preserved_and_scripts_sanitized(self):
        _tool_message, token = self._issue()
        message = self._assistant(
            f"[Odoo](https://www.odoo.com) {token} [bad](javascript:alert(1)) "
            "<script>alert(1)</script>"
        )
        body = self._formatted(message)["body"]
        self.assertIn('href="https://www.odoo.com"', body)
        self.assertIn(record_links.LINK_CLASS, body)
        self.assertNotIn("javascript:", body)
        self.assertNotIn("<script", body)

    def test_streaming_never_renders_partial_token(self):
        _tool_message, token = self._issue()
        text = f"Contact found. {token[:-2]}|Click here]] done."
        chunks = [text[i : i + 7] for i in range(0, len(text), 7)]
        events = list(
            self.thread.message_post_from_stream(
                ({"content": chunk} for chunk in chunks),
                llm_role="assistant",
            )
        )
        bodies = [
            str(event["message"]["body"])
            for event in events
            if event.get("type") in ("message_create", "message_chunk", "message_update")
        ]
        self.assertGreater(len(bodies), 3)
        for body in bodies:
            self.assertNotIn(record_links.UNAVAILABLE_CLASS, body)
            if "<a " in body:
                self.assertIn(">Click here</a>", body)
            else:
                self.assertNotIn("Click here</a>", body)
        self.assertTrue(any("[[odoo-ref:" in body for body in bodies))
        self.assertIn(">Click here</a>", bodies[-1])
        self.assertNotIn("[[odoo-ref:", bodies[-1])

    # Fallback links -------------------------------------------------------

    def test_tool_message_fallback_links(self):
        other = self.env["res.partner"].create({"name": "Second"})
        tool_message = self._run_tool(
            self.thread,
            {
                "items": [
                    record_reference(self.partner),
                    record_reference(other),
                    record_reference(self.partner),
                ]
            },
        )
        vals = self._formatted(tool_message)
        self.assertNotIn("record_refs", vals["body_json"])
        self.assertEqual(
            [(link["model"], link["id"], link["label"]) for link in vals["llm_record_links"]],
            [
                ("res.partner", self.partner.id, "Linked Partner"),
                ("res.partner", other.id, "Second"),
            ],
        )
        self.assertEqual(
            vals["llm_record_links"][0]["url"],
            f"/web#model=res.partner&id={self.partner.id}&view_type=form",
        )

    # Instruction ----------------------------------------------------------

    def test_instruction_added_once_for_chat_ui_with_tools(self):
        provider = self.provider.with_context(**{record_links.CONTEXT_KEY: True})
        base = [{"role": "system", "content": "You are helpful."}]
        messages = provider._prepare_prepend_messages(base, self.tool)
        system = [m for m in messages if m["role"] == "system"]
        self.assertEqual(len(system), 1)
        self.assertTrue(system[0]["content"].startswith("You are helpful."))
        self.assertEqual(system[0]["content"].count(RECORD_LINK_INSTRUCTION), 1)
        self.assertEqual(base[0]["content"], "You are helpful.")
        again = provider._prepare_prepend_messages(messages, self.tool)
        self.assertEqual(again[0]["content"].count(RECORD_LINK_INSTRUCTION), 1)

    def test_instruction_absent_outside_chat_ui_or_without_tools(self):
        base = [{"role": "system", "content": "You are helpful."}]
        no_flag = self.provider._prepare_prepend_messages(base, self.tool)
        self.assertNotIn(RECORD_LINK_INSTRUCTION, json.dumps(no_flag))
        provider = self.provider.with_context(**{record_links.CONTEXT_KEY: True})
        no_tools = provider._prepare_prepend_messages(base, self.env["llm.tool"])
        self.assertNotIn(RECORD_LINK_INSTRUCTION, json.dumps(no_tools))
