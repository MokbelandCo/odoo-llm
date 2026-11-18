from odoo.tests.common import TransactionCase, tagged

from odoo.addons.mail.tools.discuss import Store


@tagged("post_install", "-at_install")
class TestThreadStore(TransactionCase):
    """LLM threads must serialize through the Odoo 18 mail Store API."""

    def setUp(self):
        super().setUp()
        services = self.env["llm.provider"]._selection_service()
        if not services:
            self.skipTest("No LLM provider service is available")
        self.provider = self.env["llm.provider"].create(
            {"name": "Store Test Provider", "service": services[0][0]}
        )
        self.model = self.env["llm.model"].create(
            {
                "name": "store-test-model",
                "provider_id": self.provider.id,
                "model_use": "chat",
            }
        )
        self.threads = self.env["llm.thread"].create(
            [
                {
                    "name": f"Store thread {index}",
                    "provider_id": self.provider.id,
                    "model_id": self.model.id,
                    "user_id": self.env.user.id,
                }
                for index in range(2)
            ]
        )

    def _store_threads(self, result):
        return {
            thread["id"]: thread
            for thread in result.get("mail.thread", [])
            if thread.get("model") == "llm.thread"
        }

    def test_init_messaging_with_several_threads(self):
        store = Store()
        self.env.user._init_messaging(store)
        threads = self._store_threads(store.get_result())
        for thread in self.threads:
            self.assertIn(thread.id, threads)
            self.assertEqual(threads[thread.id]["name"], thread.name)
            self.assertEqual(threads[thread.id]["model_id"]["id"], self.model.id)
            self.assertEqual(
                threads[thread.id]["provider_id"]["id"], self.provider.id
            )

    def test_thread_data_request_list(self):
        thread = self.threads[0]
        result = Store(
            thread, as_thread=True, request_list=["model_id", "tool_ids"]
        ).get_result()
        data = self._store_threads(result)[thread.id]
        self.assertEqual(data["model_id"]["id"], self.model.id)
        self.assertEqual(data["channel_type"], "llm_chat")
