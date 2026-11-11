from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.llm_thread import record_links


@tagged("post_install", "-at_install")
class TestDummyProvider(TransactionCase):
    def test_dummy_service_is_registered(self):
        services = dict(self.env["llm.provider"]._selection_service())
        self.assertIn("dummy", services)

    def test_dummy_chat_streams_canned_reply(self):
        provider = self.env.ref("llm_dummy.llm_provider_dummy")
        model = self.env.ref("llm_dummy.llm_model_dummy_chat")
        thread = self.env["llm.thread"].create(
            {
                "name": "Dummy stream test",
                "provider_id": provider.id,
                "model_id": model.id,
            }
        )
        user_message = thread.message_post(
            body="hello popup",
            llm_role="user",
            author_id=self.env.user.partner_id.id,
        )
        chunks = list(
            provider.dummy_chat(
                user_message,
                model=model,
                stream=True,
            )
        )
        self.assertTrue(chunks)
        self.assertTrue(all("content" in chunk for chunk in chunks))
        self.assertIn("hello popup", "".join(chunk["content"] for chunk in chunks))

    def test_dummy_generate_posts_assistant_reply(self):
        provider = self.env.ref("llm_dummy.llm_provider_dummy")
        model = self.env.ref("llm_dummy.llm_model_dummy_chat")
        thread = self.env["llm.thread"].create(
            {
                "name": "Dummy generate test",
                "provider_id": provider.id,
                "model_id": model.id,
            }
        )
        events = list(thread.generate(user_message_body="hello popup"))
        self.assertTrue(any(event.get("type") == "message_create" for event in events))
        bodies = [
            str((event.get("message") or {}).get("body") or "") for event in events
        ]
        self.assertTrue(
            any("Dummy reply" in body for body in bodies),
            f"Expected dummy assistant reply in stream events, got: {bodies}",
        )


@tagged("post_install", "-at_install")
class TestDummyRecordLinks(TransactionCase):
    """End-to-end: dummy tool call -> issued reference -> rendered link."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({"name": "Dummy Link Partner"})
        cls.tool = cls.env["llm.tool"].search(
            [("name", "=", "llm_dummy_find_contact")], limit=1
        )
        cls.thread = cls.env["llm.thread"].create(
            {
                "name": "Dummy record link test",
                "provider_id": cls.env.ref("llm_dummy.llm_provider_dummy").id,
                "model_id": cls.env.ref("llm_dummy.llm_model_dummy_chat").id,
                "tool_ids": [(6, 0, cls.tool.ids)],
            }
        )

    def _generate(self, text, chat_ui=True):
        thread = self.thread.with_context(**{record_links.CONTEXT_KEY: chat_ui})
        with patch.object(type(self.env.cr), "commit", lambda cr: None):
            events = list(thread.generate(user_message_body=text))
        return events

    def _assistant_reply(self):
        message = self.thread.message_ids.filtered(
            lambda m: m.llm_role == "assistant" and m.body
        )[:1]
        return message, message.message_format()[0]

    def test_tool_is_registered(self):
        self.assertTrue(self.tool)

    def test_chat_ui_reply_renders_record_link(self):
        events = self._generate("record link for Dummy Link Partner")
        tool_message = self.thread.message_ids.filtered(lambda m: m.llm_role == "tool")
        self.assertEqual(len(tool_message), 1)
        self.assertEqual(tool_message.body_json["status"], "completed")
        links = tool_message.message_format()[0]["llm_record_links"]
        self.assertEqual(
            [(link["model"], link["id"]) for link in links],
            [("res.partner", self.partner.id)],
        )
        message, vals = self._assistant_reply()
        self.assertIn("[[odoo-ref:", message.body)
        self.assertIn(f'data-oe-id="{self.partner.id}"', vals["body"])
        self.assertIn(">Click here</a>", vals["body"])
        streamed = [
            str(event["message"]["body"])
            for event in events
            if event.get("type") == "message_update"
            and (event.get("message") or {}).get("id") == message.id
        ]
        self.assertTrue(streamed)
        self.assertIn(">Click here</a>", streamed[-1])

    def test_reply_without_token_keeps_fallback_link(self):
        self._generate("record link without link for Dummy Link Partner")
        message, vals = self._assistant_reply()
        self.assertNotIn("odoo-ref", message.body)
        self.assertNotIn("<a ", vals["body"])
        tool_message = self.thread.message_ids.filtered(lambda m: m.llm_role == "tool")
        self.assertEqual(len(tool_message.message_format()[0]["llm_record_links"]), 1)

    def test_no_instruction_outside_chat_ui(self):
        """Automation/background generation does not get the instruction."""
        self._generate("record link for Dummy Link Partner", chat_ui=False)
        message, _vals = self._assistant_reply()
        self.assertNotIn("odoo-ref", message.body)
