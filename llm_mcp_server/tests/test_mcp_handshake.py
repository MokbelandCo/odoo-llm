import json

from odoo.tests import HttpCase, TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestMcpProtocolConfiguration(TransactionCase):
    def test_protocol_negotiation(self):
        config = self.env["llm.mcp.server.config"].get_active_config()
        config.write(
            {
                "latest_protocol_version": "2025-11-25",
                "supported_protocol_versions": ["2025-06-18"],
            }
        )

        self.assertEqual(config.negotiate_protocol_version("2025-11-25"), "2025-11-25")
        self.assertEqual(config.negotiate_protocol_version("2025-06-18"), "2025-06-18")
        self.assertEqual(config.negotiate_protocol_version("unsupported"), "2025-11-25")


@tagged("post_install", "-at_install")
class TestMcpInitializeHandshake(HttpCase):
    def _url_open_json(self, url, payload, headers=None):
        return self.url_open(
            url,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json", **(headers or {})},
        )

    def test_chatgpt_protocol_reaches_initialized_state(self):
        config = self.env["llm.mcp.server.config"].get_active_config()
        self.assertEqual(config.mode, "stateful")
        self.assertTrue(config.is_protocol_version_supported("2025-11-25"))

        initialize_response = self._url_open_json(
            "/mcp",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {
                        "experimental": {"openai/visibility": {"enabled": True}}
                    },
                    "clientInfo": {"name": "openai-mcp", "version": "1.0.0"},
                },
            },
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
        )

        self.assertEqual(initialize_response.status_code, 200)
        self.assertEqual(
            initialize_response.json()["result"]["protocolVersion"], "2025-11-25"
        )
        session_id = initialize_response.headers["Mcp-Session-Id"]

        initialized_response = self._url_open_json(
            "/mcp",
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "Mcp-Protocol-Version": "2025-11-25",
                "Mcp-Session-Id": session_id,
            },
        )

        self.assertEqual(initialized_response.status_code, 202)
        session = self.env["llm.mcp.session"].sudo().get_session(session_id)
        self.assertEqual(session.state, "initialized")
        self.assertEqual(session.last_method, "notifications/initialized")
        self.assertEqual(session.request_count, 2)
