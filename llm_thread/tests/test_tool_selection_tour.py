from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestToolSelectionTour(HttpCase):
    """A new chat from the UI preselects Default tools and hides Always Include."""

    def test_tool_selection_tour(self):
        admin = self.env.ref("base.user_admin")
        env = self.env(user=admin)
        services = env["llm.provider"]._selection_service()
        provider = env["llm.provider"].create(
            {
                "name": "Tool Selection Tour Provider",
                "service": services[0][0] if services else "dummy",
            }
        )
        model = env["llm.model"].create(
            {
                "name": "tool-selection-tour-model",
                "provider_id": provider.id,
                "model_use": "chat",
            }
        )
        Tool = env["llm.tool"].with_context(active_test=False)
        Tool.search([]).write({"default": False, "always_include": "no"})

        def tool(name, **vals):
            if vals.get("always_include") in ("portal", "authenticated", "always") and "portal_ai_safe" in Tool._fields:
                vals.setdefault("portal_ai_safe", True)
            return Tool.create(
                {"name": name, "description": name, "implementation": "function", **vals}
            )

        default_tool = tool("sel_tour_default", default=True)
        tool("sel_tour_optional")
        always_tool = tool("sel_tour_always", default=True, always_include="always")
        env["llm.thread"].search([("user_id", "=", admin.id)]).unlink()
        existing = env["llm.thread"].create(
            {
                "name": "Existing Tool Selection Thread",
                "provider_id": provider.id,
                "model_id": model.id,
                "user_id": admin.id,
                "tool_ids": [(6, 0, [])],
            }
        )

        self.start_tour("/web", "llm_thread_tool_selection_tour", login="admin")

        new_thread = env["llm.thread"].search(
            [("user_id", "=", admin.id), ("id", "!=", existing.id)]
        )
        self.assertEqual(len(new_thread), 1)
        self.assertFalse(new_thread.tool_ids, "the removed Default tool stays removed")
        effective = new_thread._get_effective_tools()
        self.assertIn(always_tool, effective)
        self.assertNotIn(default_tool, effective)
