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
    def _initialize(self, path):
        return self.url_open(
            path,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "route-test", "version": "1.0.0"},
                },
            },
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
        )

    def test_chatgpt_protocol_reaches_initialized_state(self):
        config = self.env["llm.mcp.server.config"].get_active_config()
        self.assertEqual(config.mode, "stateful")
        self.assertTrue(config.is_protocol_version_supported("2025-11-25"))

        initialize_response = self.url_open(
            "/mcp",
            json={
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

        initialized_response = self.url_open(
            "/mcp",
            json={
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

    def test_multiple_server_urls_resolve_independent_configs(self):
        default = self.env["llm.mcp.server.config"].get_active_config()
        default.write({"name": "default-server", "mode": "stateless"})
        named = self.env["llm.mcp.server.config"].create(
            {
                "name": "sales-server",
                "version": "2.0.0",
                "latest_protocol_version": "2025-11-25",
                "endpoint_path": "/mcp/sales",
                "mode": "stateless",
                "active": True,
                "tool_mode": "selected",
            }
        )

        default_response = self._initialize("/mcp")
        named_response = self._initialize("/mcp/sales")
        self.assertEqual(default_response.status_code, 200, default_response.text)
        self.assertEqual(named_response.status_code, 200, named_response.text)
        self.assertEqual(
            default_response.json()["result"]["serverInfo"]["name"],
            "default-server",
        )
        self.assertEqual(
            named_response.json()["result"]["serverInfo"],
            {"name": "sales-server", "version": "2.0.0"},
        )

        health = self.url_open("/mcp/sales/health")
        self.assertEqual(health.status_code, 200, health.text)
        self.assertEqual(health.json()["server"], "sales-server")
        self.assertTrue(health.json()["endpoint"].endswith("/mcp/sales"))
        named.unlink()
