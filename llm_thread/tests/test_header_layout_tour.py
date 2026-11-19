from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestHeaderLayoutTour(HttpCase):
    """Full-screen and popup chat header settings render and work on Odoo 17."""

    def test_header_layout_tour(self):
        admin = self.env.ref("base.user_admin")
        env = self.env(user=admin)
        services = env["llm.provider"]._selection_service()
        provider = env["llm.provider"].create(
            {
                "name": "Header Tour Provider",
                "service": services[0][0] if services else "dummy",
            }
        )
        model = env["llm.model"].create(
            {
                "name": "header-tour-model",
                "provider_id": provider.id,
                "model_use": "chat",
            }
        )
        tool = env["llm.tool"].create(
            {
                "name": "header_tour_tool",
                "description": "Header tour tool",
                "implementation": "function",
            }
        )
        env["llm.thread"].search([("user_id", "=", admin.id)]).unlink()
        thread = env["llm.thread"].create(
            {
                "name": "Header Layout Tour Thread with a deliberately long title"
                " for the popup header",
                "provider_id": provider.id,
                "model_id": model.id,
                "user_id": admin.id,
                "tool_ids": [(6, 0, [])],
            }
        )

        self.start_tour("/web", "llm_thread_header_layout_tour", login="admin")
        thread.invalidate_recordset(["tool_ids"])
        self.assertIn(tool, thread.tool_ids)
