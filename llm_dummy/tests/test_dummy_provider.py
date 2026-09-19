from odoo.tests.common import TransactionCase, tagged


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
