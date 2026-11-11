from unittest.mock import patch

from odoo.tests import HttpCase, tagged

from odoo.addons.llm_tool.record_reference import record_reference


@tagged("post_install", "-at_install")
class TestRecordLinksTour(HttpCase):
    """Full-screen chat and popup navigation through record links."""

    def test_record_links_tour(self):
        admin = self.env.ref("base.user_admin")
        env = self.env(user=admin)
        services = env["llm.provider"]._selection_service()
        provider = env["llm.provider"].create(
            {"name": "Tour Provider", "service": services[0][0] if services else "dummy"}
        )
        model = env["llm.model"].create(
            {"name": "tour-model", "provider_id": provider.id, "model_use": "chat"}
        )
        tool = env["llm.tool"].create(
            {
                "name": "tour_record_link_tool",
                "description": "Tour tool",
                "implementation": "function",
            }
        )
        env["llm.thread"].search([("user_id", "=", admin.id)]).unlink()
        thread = env["llm.thread"].create(
            {
                "name": "Tour Record Link Thread",
                "provider_id": provider.id,
                "model_id": model.id,
                "user_id": admin.id,
                "tool_ids": [(6, 0, tool.ids)],
            }
        )
        partner = env["res.partner"].create({"name": "Tour Link Partner"})

        thread.message_post(body="find the partner", llm_role="user")
        tool_message = env["mail.message"].post_tool_call(
            {
                "id": "call_tour",
                "type": "function",
                "function": {"name": tool.name, "arguments": "{}"},
            },
            thread_model=thread,
        )
        with patch.object(
            type(env["llm.tool"]),
            "execute",
            autospec=True,
            return_value={"contact": record_reference(partner)},
        ):
            list(tool_message.execute_tool_call(thread_model=thread))
        token = tool_message.body_json["result"]["contact"]["link_token"]
        reply = thread.message_post(body="…", llm_role="assistant", author_id=False)
        reply.write(
            {
                "body": thread._process_llm_body(
                    f"Contact found. {token[:-2]}|Click here]] "
                    "Unknown: [[odoo-ref:ZZZZZZZZZZZZ]]"
                )
            }
        )

        self.start_tour("/web", "llm_thread_record_links_tour", login="admin")
        self.assertEqual(partner.name, "Tour Link Partner Saved")
