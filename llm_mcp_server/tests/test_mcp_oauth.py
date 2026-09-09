"""MCP OAuth 2.1 tests for metadata, tokens, PKCE, and bearer auth."""

import json
import secrets

from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.llm_mcp_server.oauth import canonical_resource_uri, pkce_challenge_s256


def _pkce_pair():
    verifier = secrets.token_urlsafe(64)
    return verifier, pkce_challenge_s256(verifier)


@tagged("post_install", "-at_install")
class TestMcpOauthModels(TransactionCase):
    def test_protected_resource_metadata(self):
        config = self.env["llm.mcp.server.config"].get_active_config()
        metadata = config.get_protected_resource_metadata()
        self.assertEqual(
            metadata["resource"], canonical_resource_uri(config.get_mcp_server_url())
        )
        self.assertTrue(metadata["authorization_servers"])
        self.assertIn("mcp:tools", metadata["scopes_supported"])

    def test_authorization_code_and_audience(self):
        user = self.env.user
        client = self.env["llm.mcp.oauth.client"].create(
            {
                "name": "Test Public Client",
                "redirect_uris": ["http://127.0.0.1:9/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
                "token_endpoint_auth_method": "none",
            }
        )
        config = self.env["llm.mcp.server.config"].get_active_config()
        resource = canonical_resource_uri(config.get_mcp_server_url())
        verifier, challenge = _pkce_pair()
        code = self.env["llm.mcp.oauth.authorization.code"].issue(
            client,
            user,
            "http://127.0.0.1:9/callback",
            challenge,
            "S256",
            resource,
            "mcp:tools",
        )
        self.assertTrue(
            code.is_valid(client, "http://127.0.0.1:9/callback", verifier, resource)
        )
        self.assertFalse(
            code.is_valid(client, "http://127.0.0.1:9/callback", "wrong", resource)
        )
        token = self.env["llm.mcp.oauth.token"].issue(
            client, user, resource, "mcp:tools", with_refresh=True
        )
        self.assertTrue(token.is_access_valid(resource))
        self.assertFalse(token.is_access_valid("https://other.example/mcp"))

    def test_client_secret_hashing(self):
        client = self.env["llm.mcp.oauth.client"].create({"name": "Confidential"})
        client.set_client_secret("s3cret")
        self.assertTrue(client.is_confidential)
        self.assertTrue(client.check_secret("s3cret"))
        self.assertFalse(client.check_secret("nope"))


@tagged("post_install", "-at_install")
class TestMcpOauthHttp(HttpCase):
    def test_well_known_metadata(self):
        response = self.url_open("/.well-known/oauth-protected-resource/mcp")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["resource"].endswith("/mcp"))
        self.assertTrue(body["authorization_servers"])

        as_response = self.url_open("/.well-known/oauth-authorization-server")
        self.assertEqual(as_response.status_code, 200)
        as_body = as_response.json()
        self.assertIn("/mcp/oauth/authorize", as_body["authorization_endpoint"])
        self.assertIn("S256", as_body["code_challenge_methods_supported"])
        self.assertIn("authorization_code", as_body["grant_types_supported"])

    def test_dynamic_client_registration_and_client_credentials(self):
        register = self.url_open(
            "/mcp/oauth/register",
            json={
                "client_name": "Http Test Client",
                "redirect_uris": ["http://127.0.0.1:9/callback"],
                "grant_types": ["client_credentials", "authorization_code", "refresh_token"],
                "token_endpoint_auth_method": "client_secret_post",
            },
        )
        self.assertEqual(register.status_code, 201)
        payload = register.json()
        self.assertTrue(payload["client_id"])
        self.assertTrue(payload["client_secret"])

        client = (
            self.env["llm.mcp.oauth.client"]
            .sudo()
            .search([("client_id", "=", payload["client_id"])], limit=1)
        )
        client.service_user_id = self.env.ref("base.user_admin")
        client.grant_types = [
            "client_credentials",
            "authorization_code",
            "refresh_token",
        ]

        config = self.env["llm.mcp.server.config"].sudo().get_active_config()
        token_response = self.url_open(
            "/mcp/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": payload["client_id"],
                "client_secret": payload["client_secret"],
                "resource": config.get_mcp_server_url(),
            },
        )
        self.assertEqual(token_response.status_code, 200, token_response.text)
        token = token_response.json()["access_token"]

        initialize = self.url_open(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "oauth-test", "version": "1.0.0"},
                },
            },
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(initialize.status_code, 200, initialize.text)
        session_id = initialize.headers.get("Mcp-Session-Id")

        tools = self.url_open(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            },
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                **({"Mcp-Session-Id": session_id} if session_id else {}),
            },
        )
        self.assertEqual(tools.status_code, 200, tools.text)
        self.assertIn("tools", tools.json()["result"])

    def test_tools_list_without_token_advertises_resource_metadata(self):
        response = self.url_open(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/list",
                "params": {},
            },
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(response.status_code, 401)
        challenge = response.headers.get("WWW-Authenticate", "")
        self.assertIn("resource_metadata=", challenge)
        self.assertIn("oauth-protected-resource", challenge)

    def test_authorization_code_token_exchange(self):
        verifier, challenge = _pkce_pair()
        client = self.env["llm.mcp.oauth.client"].sudo().create(
            {
                "name": "Code Client",
                "redirect_uris": ["http://127.0.0.1:9/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
                "token_endpoint_auth_method": "none",
            }
        )
        config = self.env["llm.mcp.server.config"].sudo().get_active_config()
        resource = canonical_resource_uri(config.get_mcp_server_url())
        admin = self.env.ref("base.user_admin")
        code = (
            self.env["llm.mcp.oauth.authorization.code"]
            .sudo()
            .issue(
                client,
                admin,
                "http://127.0.0.1:9/callback",
                challenge,
                "S256",
                resource,
                "mcp:tools",
            )
        )
        token_response = self.url_open(
            "/mcp/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": client.client_id,
                "code": code.code,
                "redirect_uri": "http://127.0.0.1:9/callback",
                "code_verifier": verifier,
                "resource": resource,
            },
        )
        self.assertEqual(token_response.status_code, 200, token_response.text)
        body = token_response.json()
        self.assertEqual(body["token_type"], "Bearer")
        self.assertTrue(body["access_token"])
        self.assertTrue(body["refresh_token"])
        self.assertEqual(canonical_resource_uri(body["resource"]), resource)
