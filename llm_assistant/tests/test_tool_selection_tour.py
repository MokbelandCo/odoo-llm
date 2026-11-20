from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestAssistantToolSelectionTour(HttpCase):
    """The assistant form preselects Default tools and hides Always Include."""

    def test_assistant_tool_selection_tour(self):
        env = self.env(user=self.env.ref("base.user_admin"))
        Tool = env["llm.tool"].with_context(active_test=False)
        Tool.search([]).write({"default": False, "always_include": "no"})

        def tool(name, **vals):
            if vals.get("always_include") in ("portal", "authenticated", "always") and "portal_ai_safe" in Tool._fields:
                vals.setdefault("portal_ai_safe", True)
            return Tool.create(
                {"name": name, "description": name, "implementation": "function", **vals}
            )

        default_tool = tool("sel_tour_default", default=True)
        optional_tool = tool("sel_tour_optional")
        tool("sel_tour_always", default=True, always_include="always")
        env["llm.prompt"].create(
            {
                "name": "Tool Selection Tour Prompt",
                "template": "You are a tour assistant.",
                "format": "text",
            }
        )

        self.start_tour("/web", "llm_assistant_tool_selection_tour", login="admin")

        assistant = env["llm.assistant"].search(
            [("name", "=", "Tool Selection Tour Assistant")]
        )
        self.assertEqual(len(assistant), 1)
        self.assertEqual(assistant.tool_ids, optional_tool)
        self.assertNotIn(default_tool, assistant.tool_ids)
