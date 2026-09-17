from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestThreadAccess(TransactionCase):
    """Standard users may only access their own llm.thread records."""

    def setUp(self):
        super().setUp()
        services = self.env["llm.provider"]._selection_service()
        if not services:
            self.skipTest("No LLM provider service is available")
        self.provider = self.env["llm.provider"].create(
            {
                "name": "Thread Access Test Provider",
                "service": services[0][0],
            }
        )
        self.model = self.env["llm.model"].create(
            {
                "name": "thread-access-test-model",
                "provider_id": self.provider.id,
                "model_use": "chat",
            }
        )
        self.user_a = new_test_user(
            self.env,
            login="llm_thread_user_a",
            groups="base.group_user",
        )
        self.user_b = new_test_user(
            self.env,
            login="llm_thread_user_b",
            groups="base.group_user",
        )
        self.thread_a = (
            self.env["llm.thread"]
            .with_user(self.user_a)
            .create(
                {
                    "name": "User A Thread",
                    "provider_id": self.provider.id,
                    "model_id": self.model.id,
                    "user_id": self.user_a.id,
                }
            )
        )

    def test_user_cannot_read_another_users_thread(self):
        threads = (
            self.env["llm.thread"]
            .with_user(self.user_b)
            .search([("id", "=", self.thread_a.id)])
        )
        self.assertFalse(threads)

    def test_user_cannot_browse_another_users_thread(self):
        thread = self.env["llm.thread"].with_user(self.user_b).browse(self.thread_a.id)
        self.assertFalse(thread.exists())

    def test_user_cannot_write_another_users_thread(self):
        with self.assertRaises(AccessError):
            self.thread_a.with_user(self.user_b).write({"name": "Hijacked"})
