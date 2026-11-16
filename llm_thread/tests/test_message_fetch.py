import json
from unittest.mock import patch

from odoo.tests.common import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestMessageFetchOdoo18(HttpCase):
    """Regression: Odoo 18 mail.message._message_fetch calling convention."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Provider = type(cls.env["llm.provider"])
        original_services = Provider._get_available_services

        def _services_with_test(self):
            return list(original_services(self)) + [
                ("message_fetch_test", "Message Fetch Test")
            ]

        patcher = patch.object(Provider, "_get_available_services", _services_with_test)
        patcher.start()
        cls.addClassCleanup(patcher.stop)

        cls.provider = cls.env["llm.provider"].create(
            {
                "name": "Message Fetch Test Provider",
                "service": "message_fetch_test",
            }
        )
        cls.model = cls.env["llm.model"].create(
            {
                "name": "message-fetch-test-model",
                "provider_id": cls.provider.id,
                "model_use": "chat",
            }
        )
        cls.user_a = new_test_user(
            cls.env,
            login="msgfetch_a",
            groups="base.group_user",
        )
        cls.user_b = new_test_user(
            cls.env,
            login="msgfetch_b",
            groups="base.group_user",
        )

        cls.partner = cls.env["res.partner"].create({"name": "Message Fetch Partner"})
        cls.partner_comment = cls.partner.message_post(
            body="Ordinary partner comment",
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )
        cls.partner_note = cls.partner.message_post(
            body="Ordinary partner note",
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )
        cls.page_alpha_one = cls.partner.message_post(
            body="alpha one",
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )
        cls.page_alpha_two = cls.partner.message_post(
            body="alpha two",
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )
        cls.page_beta_three = cls.partner.message_post(
            body="beta three",
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )
        cls.page_gamma_four = cls.partner.message_post(
            body="gamma four",
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )
        cls.page_messages = (
            cls.page_alpha_one
            | cls.page_alpha_two
            | cls.page_beta_three
            | cls.page_gamma_four
        )

        cls.thread = (
            cls.env["llm.thread"]
            .with_user(cls.user_a)
            .create(
                {
                    "name": "Message Fetch Thread",
                    "provider_id": cls.provider.id,
                    "model_id": cls.model.id,
                    "user_id": cls.user_a.id,
                }
            )
        )
        cls.msg_user = cls.thread.message_post(
            body="Please summarize the invoice",
            llm_role="user",
        )
        cls.msg_assistant = cls.thread.message_post(
            body="Invoice summary from the assistant",
            llm_role="assistant",
        )
        cls.msg_tool = cls.thread.message_post(
            body="tool payload",
            llm_role="tool",
            body_json={"ok": True},
        )
        cls.msg_system = cls.thread.message_post(
            body="system instructions",
            llm_role="system",
        )
        cls.msg_noise = cls.thread.message_post(
            body="Please summarize the invoice (noise note without llm_role)",
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )
        cls.role_messages = (
            cls.msg_user | cls.msg_assistant | cls.msg_tool | cls.msg_system
        )

    def _controller_domain(self, record):
        return [
            ("res_id", "=", record.id),
            ("model", "=", record._name),
            ("message_type", "!=", "user_notification"),
        ]

    def _fetch(self, record, **kwargs):
        return self.env["mail.message"]._message_fetch(
            self._controller_domain(record),
            **kwargs,
        )

    def test_ordinary_chatter_returns_comment_and_note(self):
        result = self._fetch(self.partner)
        messages = result["messages"]
        self.assertIn(self.partner_comment, messages)
        self.assertIn(self.partner_note, messages)
        self.assertFalse(self.partner_comment.llm_role)
        self.assertFalse(self.partner_note.llm_role)

    def test_positional_call_and_search_term_alpha(self):
        domain = self._controller_domain(self.partner)
        Message = self.env["mail.message"]
        result = Message._message_fetch(domain, None, None, None, None, 30)
        self.assertIn(self.partner_note, result["messages"])

        searched = Message._message_fetch(domain, "alpha", None, None, None, 30)
        self.assertEqual(searched["count"], 2)
        self.assertEqual(
            set(searched["messages"].ids),
            {self.page_alpha_one.id, self.page_alpha_two.id},
        )

    def test_odoo19_keywords_accepted_without_crash(self):
        domain = self._controller_domain(self.partner)
        Message = self.env["mail.message"]

        result_none = Message._message_fetch(
            domain,
            thread=None,
            is_notification=False,
        )
        self.assertIn(self.partner_note, result_none["messages"])

        result_thread = Message._message_fetch(
            domain,
            thread=self.partner,
            is_notification=True,
        )
        self.assertIn(self.partner_note, result_thread["messages"])

    def test_search_pagination_before_after_around_limit(self):
        domain = self._controller_domain(self.partner)
        Message = self.env["mail.message"]
        page_domain = domain + [("id", "in", self.page_messages.ids)]

        searched = Message._message_fetch(
            page_domain,
            search_term="alpha",
        )
        self.assertEqual(searched["count"], 2)

        before = Message._message_fetch(
            page_domain,
            before=self.page_beta_three.id,
        )
        self.assertEqual(
            before["messages"].ids,
            [self.page_alpha_two.id, self.page_alpha_one.id],
        )

        after = Message._message_fetch(
            page_domain,
            after=self.page_alpha_two.id,
        )
        self.assertEqual(
            after["messages"].ids,
            [self.page_gamma_four.id, self.page_beta_three.id],
        )

        around = Message._message_fetch(
            page_domain,
            around=self.page_beta_three.id,
            limit=2,
        )
        self.assertEqual(
            set(around["messages"].ids),
            {self.page_beta_three.id, self.page_gamma_four.id},
        )

        limited = Message._message_fetch(page_domain, limit=2)
        self.assertEqual(
            limited["messages"].ids,
            [self.page_gamma_four.id, self.page_beta_three.id],
        )

    def test_llm_thread_role_filter_and_search(self):
        result = self._fetch(self.thread)
        messages = result["messages"]
        for message in self.role_messages:
            self.assertIn(message, messages)
        self.assertNotIn(self.msg_noise, messages)

        searched = self._fetch(self.thread, search_term="summarize the invoice")
        self.assertEqual(searched["count"], 1)
        self.assertEqual(searched["messages"], self.msg_user)
        self.assertNotIn(self.msg_noise, searched["messages"])

    def test_access_rules_owner_vs_other_user(self):
        owner_fetch = (
            self.env["mail.message"]
            .with_user(self.user_a)
            ._message_fetch(self._controller_domain(self.thread))
        )
        self.assertEqual(
            set(owner_fetch["messages"].ids),
            set(self.role_messages.ids),
        )

        other_fetch = (
            self.env["mail.message"]
            .with_user(self.user_b)
            ._message_fetch(self._controller_domain(self.thread))
        )
        self.assertFalse(
            set(other_fetch["messages"].ids) & set(self.role_messages.ids)
        )

        owner_partner = (
            self.env["mail.message"]
            .with_user(self.user_a)
            ._message_fetch(self._controller_domain(self.partner))
        )
        self.assertIn(self.partner_note, owner_partner["messages"])

    def _jsonrpc_thread_messages(self, login, thread_model, thread_id):
        self.authenticate(login, login)
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "id": 1,
            "params": {
                "thread_model": thread_model,
                "thread_id": thread_id,
            },
        }
        response = self.url_open(
            "/mail/thread/messages",
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertNotIn("error", body, body)
        return body["result"]

    def test_mail_thread_messages_http_route(self):
        partner_result = self._jsonrpc_thread_messages(
            "msgfetch_a",
            "res.partner",
            self.partner.id,
        )
        partner_ids = set(partner_result["messages"])
        self.assertIn(self.partner_comment.id, partner_ids)
        self.assertIn(self.partner_note.id, partner_ids)

        llm_result = self._jsonrpc_thread_messages(
            "msgfetch_a",
            "llm.thread",
            self.thread.id,
        )
        llm_ids = set(llm_result["messages"])
        self.assertTrue(set(self.role_messages.ids) <= llm_ids)
        self.assertNotIn(self.msg_noise.id, llm_ids)

        stored_messages = llm_result["data"].get("mail.message", [])
        assistant_data = next(
            item for item in stored_messages if item["id"] == self.msg_assistant.id
        )
        self.assertEqual(assistant_data.get("llm_role"), "assistant")

        other_result = self._jsonrpc_thread_messages(
            "msgfetch_b",
            "llm.thread",
            self.thread.id,
        )
        other_ids = set(other_result["messages"])
        self.assertFalse(set(self.role_messages.ids) & other_ids)
