from datetime import timedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestMcpAuthenticationPolicyConfiguration(TransactionCase):
    def test_new_configs_default_to_protected_endpoint(self):
        defaults = self.env["llm.mcp.server.config"].default_get(
            ["authentication_policy"]
        )
        self.assertEqual(defaults["authentication_policy"], "protected")

    def test_protected_endpoint_requires_an_authentication_mechanism(self):
        config = self.env["llm.mcp.server.config"].get_active_config()
        config.authentication_policy = "operations"
        config.write({"oauth_enabled": False, "allow_api_key": False})

        with self.assertRaises(ValidationError):
            config.write({"authentication_policy": "protected"})


@tagged("post_install", "-at_install")
class TestMcpAuthenticationPolicyHttp(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.env["llm.mcp.server.config"].get_active_config()
        cls.admin = cls.env.ref("base.user_admin")
        cls.api_key = (
            cls.env["res.users.apikeys"]
            .with_user(cls.admin)
            ._generate(
                scope="rpc",
                name="MCP authentication policy tests",
                expiration_date=fields.Datetime.now() + timedelta(days=1),
            )
        )

    def setUp(self):
        super().setUp()
        self.config.write(
            {
                "authentication_policy": "protected",
                "oauth_enabled": True,
                "allow_api_key": True,
                "mode": "stateful",
                "tool_mode": "all",
            }
        )

    def _request(
        self,
        method,
        *,
        path="/mcp",
        authenticated=False,
        session_id=None,
        request_id=1,
        params=None,
    ):
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if authenticated:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
        }
        if request_id is not None:
            payload["id"] = request_id
        return self.url_open(path, json=payload, headers=headers)

    def _assert_bearer_challenge(self, response, metadata_url=None):
        self.assertEqual(response.status_code, 401, response.text)
        challenge = response.headers.get("WWW-Authenticate", "")
        self.assertIn("Bearer", challenge)
        if metadata_url:
            self.assertIn("resource_metadata=", challenge)
            self.assertIn(metadata_url, challenge)

    def _tool_call_params(self):
        return {
            "name": "odoo_record_retriever",
            "arguments": {
                "model": "res.users",
                "domain": [["id", "=", self.admin.id]],
                "fields": ["name"],
                "limit": 1,
            },
        }

    def test_protected_endpoint_rejects_every_public_protocol_method(self):
        for mode in ("stateful", "stateless"):
            self.config.mode = mode
            for method in (
                "initialize",
                "notifications/initialized",
                "ping",
                "tools/list",
                "tools/call",
            ):
                with self.subTest(mode=mode, method=method):
                    response = self._request(
                        method,
                        request_id=(
                            None if method == "notifications/initialized" else 1
                        ),
                    )
                    self._assert_bearer_challenge(
                        response,
                        self.config.get_resource_metadata_url(),
                    )

    def test_protected_stateful_session_is_bound_during_initialize(self):
        initialize = self._request(
            "initialize",
            authenticated=True,
            params={
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "policy-test", "version": "1.0"},
            },
        )
        self.assertEqual(initialize.status_code, 200, initialize.text)
        session_id = initialize.headers["Mcp-Session-Id"]
        session = (
            self.env["llm.mcp.session"]
            .sudo()
            .get_session(session_id, server_config=self.config)
        )
        self.assertEqual(session.user_id, self.admin)

        initialized = self._request(
            "notifications/initialized",
            authenticated=True,
            session_id=session_id,
            request_id=None,
        )
        self.assertEqual(initialized.status_code, 202, initialized.text)

        ping = self._request(
            "ping",
            authenticated=True,
            session_id=session_id,
        )
        self.assertEqual(ping.status_code, 200, ping.text)

        tools = self._request(
            "tools/list",
            authenticated=True,
            session_id=session_id,
        )
        self.assertEqual(tools.status_code, 200, tools.text)
        self.assertIn("tools", tools.json()["result"])

        call = self._request(
            "tools/call",
            authenticated=True,
            session_id=session_id,
            params=self._tool_call_params(),
        )
        self.assertEqual(call.status_code, 200, call.text)
        self.assertFalse(call.json()["result"]["isError"])

    def test_protected_stateless_requests_accept_api_key(self):
        self.config.mode = "stateless"
        for method, params in (
            ("initialize", {"protocolVersion": "2025-11-25"}),
            ("notifications/initialized", {}),
            ("ping", {}),
            ("tools/list", {}),
            ("tools/call", self._tool_call_params()),
        ):
            with self.subTest(method=method):
                response = self._request(
                    method,
                    authenticated=True,
                    request_id=(None if method == "notifications/initialized" else 1),
                    params=params,
                )
                self.assertIn(response.status_code, (200, 202), response.text)

    def test_operations_policy_only_exposes_explicit_public_methods(self):
        self.config.write({"authentication_policy": "operations", "mode": "stateless"})
        for method in ("initialize", "notifications/initialized", "ping"):
            with self.subTest(method=method):
                response = self._request(
                    method,
                    request_id=(None if method == "notifications/initialized" else 1),
                    params=(
                        {"protocolVersion": "2025-11-25"}
                        if method == "initialize"
                        else {}
                    ),
                )
                self.assertIn(response.status_code, (200, 202), response.text)

        for method in ("tools/list", "tools/call", "resources/list"):
            with self.subTest(method=method):
                response = self._request(method)
                self._assert_bearer_challenge(
                    response,
                    self.config.get_resource_metadata_url(),
                )

        tools = self._request("tools/list", authenticated=True)
        self.assertEqual(tools.status_code, 200, tools.text)
        call = self._request(
            "tools/call",
            authenticated=True,
            params=self._tool_call_params(),
        )
        self.assertEqual(call.status_code, 200, call.text)
        self.assertFalse(call.json()["result"]["isError"])

    def test_named_server_challenge_uses_its_own_metadata(self):
        named = self.env["llm.mcp.server.config"].create(
            {
                "name": "Named protected server",
                "version": "1.0.0",
                "latest_protocol_version": "2025-11-25",
                "endpoint_path": "/mcp/policy-test",
                "mode": "stateless",
                "oauth_enabled": True,
                "allow_api_key": False,
            }
        )
        response = self._request("initialize", path=named.endpoint_path)
        self._assert_bearer_challenge(
            response,
            named.get_resource_metadata_url(),
        )

        metadata = self.url_open(
            "/.well-known/oauth-protected-resource/mcp/policy-test"
        )
        self.assertEqual(metadata.status_code, 200, metadata.text)
        self.assertEqual(
            metadata.json()["resource"],
            named.get_protected_resource_metadata()["resource"],
        )
        named.unlink()

    def test_oauth_disabled_uses_api_keys_without_advertising_discovery(self):
        self.config.write({"oauth_enabled": False, "allow_api_key": True})
        challenge = self._request("initialize")
        self._assert_bearer_challenge(challenge)
        self.assertNotIn(
            "resource_metadata",
            challenge.headers.get("WWW-Authenticate", ""),
        )

        metadata = self.url_open("/.well-known/oauth-protected-resource/mcp")
        self.assertEqual(metadata.status_code, 404, metadata.text)

        initialize = self._request(
            "initialize",
            authenticated=True,
            params={"protocolVersion": "2025-11-25"},
        )
        self.assertEqual(initialize.status_code, 200, initialize.text)

    def test_api_key_is_rejected_when_disabled(self):
        self.config.write({"oauth_enabled": True, "allow_api_key": False})
        response = self._request("initialize", authenticated=True)
        self._assert_bearer_challenge(
            response,
            self.config.get_resource_metadata_url(),
        )

    def test_all_protected_resource_metadata_routes(self):
        named = self.env["llm.mcp.server.config"].create(
            {
                "name": "Metadata route server",
                "version": "1.0.0",
                "latest_protocol_version": "2025-11-25",
                "endpoint_path": "/mcp/metadata-test",
                "mode": "stateless",
            }
        )
        for path, suffix in (
            ("/.well-known/oauth-protected-resource", "/mcp"),
            ("/.well-known/oauth-protected-resource/mcp", "/mcp"),
            (
                "/.well-known/oauth-protected-resource/mcp/metadata-test",
                "/mcp/metadata-test",
            ),
        ):
            with self.subTest(path=path):
                response = self.url_open(path)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(response.json()["resource"].endswith(suffix))
        named.unlink()
