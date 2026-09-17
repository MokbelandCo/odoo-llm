from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestAssistantAccess(TransactionCase):
    """Assistant listing and set_assistant must honor is_public / allowed groups."""

    def setUp(self):
        super().setUp()
        self.prompt = self.env["llm.prompt"].create(
            {
                "name": "Popup Access Test Prompt",
                "template": "You are a test assistant.",
                "format": "text",
            }
        )
        self.public_assistant = self.env["llm.assistant"].create(
            {
                "name": "Public Test Assistant",
                "prompt_id": self.prompt.id,
                "is_public": True,
            }
        )
        self.private_assistant = self.env["llm.assistant"].create(
            {
                "name": "Private Test Assistant",
                "prompt_id": self.prompt.id,
                "is_public": False,
            }
        )
        self.group = self.env["res.groups"].create(
            {
                "name": "LLM Popup Test Group",
                "privilege_id": self.env.ref("llm.privilege_llm").id,
            }
        )
        self.group_assistant = self.env["llm.assistant"].create(
            {
                "name": "Group Test Assistant",
                "prompt_id": self.prompt.id,
                "is_public": False,
                "allowed_group_ids": [(6, 0, [self.group.id])],
            }
        )
        self.user = new_test_user(
            self.env,
            login="llm_popup_user",
            groups="base.group_user",
        )
        self.grouped_user = new_test_user(
            self.env,
            login="llm_popup_grouped_user",
            groups="base.group_user",
        )
        self.grouped_user.write({"group_ids": [(4, self.group.id)]})

    def test_standard_user_lists_only_public_assistants(self):
        allowed = (
            self.env["llm.assistant"].with_user(self.user).get_allowed_assistants()
        )
        names = {item["name"] for item in allowed}
        self.assertIn("Public Test Assistant", names)
        self.assertNotIn("Private Test Assistant", names)
        self.assertNotIn("Group Test Assistant", names)

    def test_group_user_lists_group_assistant(self):
        allowed = (
            self.env["llm.assistant"]
            .with_user(self.grouped_user)
            .get_allowed_assistants()
        )
        names = {item["name"] for item in allowed}
        self.assertIn("Public Test Assistant", names)
        self.assertIn("Group Test Assistant", names)
        self.assertNotIn("Private Test Assistant", names)

    def test_search_read_does_not_expose_private_assistant(self):
        records = (
            self.env["llm.assistant"]
            .with_user(self.user)
            .search_read(
                [("id", "in", [self.public_assistant.id, self.private_assistant.id])],
                ["name"],
            )
        )
        names = {item["name"] for item in records}
        self.assertIn("Public Test Assistant", names)
        self.assertNotIn("Private Test Assistant", names)

    def test_set_assistant_rejects_unauthorized_assistant(self):
        provider, model = self._make_provider_and_model()
        if not provider:
            self.skipTest("No LLM provider service is available")
        thread = (
            self.env["llm.thread"]
            .with_user(self.user)
            .create(
                {
                    "name": "User Thread",
                    "provider_id": provider.id,
                    "model_id": model.id,
                    "user_id": self.user.id,
                }
            )
        )
        # Record rules hide the assistant, and set_assistant must still refuse
        # a manually supplied ID.
        self.assertFalse(thread.set_assistant(self.private_assistant.id))
        self.assertFalse(thread.assistant_id)
        assistant, error = (
            self.env["llm.assistant"]
            .with_user(self.user)
            .get_assistant_by_id(self.private_assistant.id)
        )
        self.assertFalse(assistant)
        self.assertTrue(error)

    def test_set_assistant_allows_public_assistant(self):
        provider, model = self._make_provider_and_model()
        if not provider:
            self.skipTest("No LLM provider service is available")
        thread = (
            self.env["llm.thread"]
            .with_user(self.user)
            .create(
                {
                    "name": "User Thread Public Assistant",
                    "provider_id": provider.id,
                    "model_id": model.id,
                    "user_id": self.user.id,
                }
            )
        )
        self.assertTrue(thread.set_assistant(self.public_assistant.id))
        self.assertEqual(thread.assistant_id, self.public_assistant)

    def _make_provider_and_model(self):
        services = self.env["llm.provider"]._selection_service()
        if not services:
            return None, None
        provider = self.env["llm.provider"].create(
            {
                "name": "Assistant Access Test Provider",
                "service": services[0][0],
            }
        )
        model = self.env["llm.model"].create(
            {
                "name": "assistant-access-test-model",
                "provider_id": provider.id,
                "model_use": "chat",
            }
        )
        return provider, model
