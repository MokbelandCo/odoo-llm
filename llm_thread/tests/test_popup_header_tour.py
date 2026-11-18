from odoo.tests.common import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestPopupHeaderTour(HttpCase):
    """Open the systray AI popup and change the model from its compact header."""

    def setUp(self):
        super().setUp()
        services = self.env["llm.provider"]._selection_service()
        if not services:
            self.skipTest("No LLM provider service is available")
        self.env["llm.provider"].search([]).write({"active": False})
        self.provider = self.env["llm.provider"].create(
            {"name": "Tour Provider", "service": services[0][0]}
        )
        self.model_alpha = self.env["llm.model"].create(
            {
                "name": "tour-model-alpha",
                "provider_id": self.provider.id,
                "model_use": "chat",
                "default": True,
            }
        )
        self.model_beta = self.env["llm.model"].create(
            {
                "name": "tour-model-beta",
                "provider_id": self.provider.id,
                "model_use": "chat",
            }
        )
        admin = self.env.ref("base.user_admin")
        # Several existing threads: init_messaging must serialize all of them.
        self.env["llm.thread"].create(
            [
                {
                    "name": f"Existing tour chat {index}",
                    "provider_id": self.provider.id,
                    "model_id": self.model_alpha.id,
                    "user_id": admin.id,
                }
                for index in range(2)
            ]
        )

    def test_popup_compact_header_changes_model(self):
        existing = self.env["llm.thread"].search([])
        self.start_tour("/odoo", "llm_thread_popup_header_tour", login="admin")
        created = self.env["llm.thread"].search([("id", "not in", existing.ids)])
        self.assertEqual(len(created), 1)
        self.assertEqual(created.model_id, self.model_beta)
