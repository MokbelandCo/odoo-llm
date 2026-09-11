"""MCP OAuth 2.1 tests for metadata, tokens, PKCE, and bearer auth."""

import json
import secrets
from urllib.parse import parse_qs, urlencode, urlparse

from lxml import html as html_parser

from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.llm_mcp_server.oauth import (
    canonical_resource_uri,
    pkce_challenge_s256,
    redirect_uri_with_params,
)


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

    def test_redirect_uri_with_params(self):
        url = redirect_uri_with_params(
            "https://chatgpt.com/connector/oauth/TcOKfq1os1i5",
            {"code": "abc", "state": "oauth_s_1"},
        )
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        self.assertEqual(parsed.netloc, "chatgpt.com")
        self.assertEqual(query["code"], ["abc"])
        self.assertEqual(query["state"], ["oauth_s_1"])

        with_existing = redirect_uri_with_params(
            "http://127.0.0.1:9/callback?foo=bar",
            {"error": "access_denied"},
        )
        self.assertIn("foo=bar", with_existing)
        self.assertIn("error=access_denied", with_existing)


@tagged("post_install", "-at_install")
class TestMcpOauthHttp(HttpCase):
    def _url_open_json(self, url, payload, headers=None):
        return self.url_open(
            url,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json", **(headers or {})},
        )

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
        register = self._url_open_json(
            "/mcp/oauth/register",
            {
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

        initialize = self._url_open_json(
            "/mcp",
            {
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

        tools = self._url_open_json(
            "/mcp",
            {
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
        response = self._url_open_json(
            "/mcp",
            {
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

    def _authorize_client(self, redirect_uri="http://127.0.0.1:9/callback", name="Browser Client"):
        return self.env["llm.mcp.oauth.client"].sudo().create(
            {
                "name": name,
                "redirect_uris": [redirect_uri],
                "grant_types": ["authorization_code", "refresh_token"],
                "token_endpoint_auth_method": "none",
            }
        )

    def _authorize_params(self, client, challenge, redirect_uri=None, extra=None):
        config = self.env["llm.mcp.server.config"].sudo().get_active_config()
        params = {
            "response_type": "code",
            "client_id": client.client_id,
            "redirect_uri": redirect_uri or client.redirect_uris[0],
            "scope": "openid mcp:tools",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": canonical_resource_uri(config.get_mcp_server_url()),
            "state": "oauth_s_test_state",
            "ui_locales": "en-US",
        }
        if extra:
            params.update(extra)
        return params

    def _csrf_token(self, response):
        tree = html_parser.fromstring(response.content)
        values = tree.xpath('//input[@name="csrf_token"]/@value')
        self.assertTrue(values, "Authorize form is missing a CSRF token")
        return values[0]

    def test_authorize_requires_login(self):
        _verifier, challenge = _pkce_pair()
        client = self._authorize_client()
        params = self._authorize_params(client, challenge)
        response = self.url_open(
            "/mcp/oauth/authorize?" + urlencode(params),
            allow_redirects=False,
        )
        self.assertIn(response.status_code, (301, 302, 303, 307))
        self.assertIn("/web/login", response.headers.get("Location", ""))

    def test_authorize_page_renders_for_logged_in_user(self):
        """Consent page must render even when Website is installed.

        web.login_layout is rewritten to website.layout, which used to raise
        KeyError: 'website' on this backend auth='user' route.
        """
        self.authenticate("admin", "admin")
        _verifier, challenge = _pkce_pair()
        client = self._authorize_client(name="ChatGPT Connector")
        params = self._authorize_params(client, challenge)
        response = self.url_open("/mcp/oauth/authorize?" + urlencode(params))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(b"Internal Server Error", response.content)
        self.assertNotIn(b"KeyError", response.content)
        self.assertIn(b"o_mcp_oauth", response.content)
        self.assertIn(b"o_mcp_oauth_authorize", response.content)
        self.assertNotIn(b"oe_website_login_container", response.content)
        self.assertIn(b"Authorize MCP client", response.content)
        self.assertIn(b"ChatGPT Connector", response.content)
        self.assertIn(b"openid mcp:tools", response.content)
        self.assertIn(b'name="allow"', response.content)
        self.assertIn(b'name="deny"', response.content)

    def test_authorize_unknown_client_renders_error_page(self):
        self.authenticate("admin", "admin")
        _verifier, challenge = _pkce_pair()
        params = {
            "response_type": "code",
            "client_id": "not-a-registered-client",
            "redirect_uri": "http://127.0.0.1:9/callback",
            "scope": "mcp:tools",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": canonical_resource_uri(
                self.env["llm.mcp.server.config"].sudo().get_active_config().get_mcp_server_url()
            ),
            "state": "oauth_s_unknown",
        }
        response = self.url_open("/mcp/oauth/authorize?" + urlencode(params))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(b"Internal Server Error", response.content)
        self.assertNotIn(b"KeyError", response.content)
        self.assertIn(b"o_mcp_oauth_error", response.content)
        self.assertIn(b"Unknown client", response.content)

    def test_authorize_consent_allow_exchanges_code_and_calls_mcp(self):
        self.authenticate("admin", "admin")
        verifier, challenge = _pkce_pair()
        redirect_uri = "http://127.0.0.1:9/callback"
        client = self._authorize_client(redirect_uri=redirect_uri)
        params = self._authorize_params(client, challenge, redirect_uri=redirect_uri)

        page = self.url_open("/mcp/oauth/authorize?" + urlencode(params))
        self.assertEqual(page.status_code, 200, page.text)
        csrf_token = self._csrf_token(page)

        consent = self.url_open(
            "/mcp/oauth/authorize",
            data={
                "csrf_token": csrf_token,
                "client_id": params["client_id"],
                "redirect_uri": redirect_uri,
                "state": params["state"],
                "scope": params["scope"],
                "resource": params["resource"],
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "response_type": "code",
                "allow": "1",
            },
            allow_redirects=False,
        )
        self.assertIn(consent.status_code, (301, 302, 303, 307), consent.text)
        location = consent.headers.get("Location", "")
        self.assertTrue(location.startswith(redirect_uri), location)
        query = parse_qs(urlparse(location).query)
        self.assertEqual(query.get("state"), [params["state"]])
        self.assertTrue(query.get("code"), location)
        code = query["code"][0]

        token_response = self.url_open(
            "/mcp/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": client.client_id,
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
                "resource": params["resource"],
            },
        )
        self.assertEqual(token_response.status_code, 200, token_response.text)
        access_token = token_response.json()["access_token"]

        initialize = self._url_open_json(
            "/mcp",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "oauth-authorize-test", "version": "1.0.0"},
                },
            },
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(initialize.status_code, 200, initialize.text)
        self.assertEqual(
            initialize.json()["result"]["protocolVersion"], "2025-11-25"
        )

    def test_authorize_consent_deny_redirects_with_access_denied(self):
        self.authenticate("admin", "admin")
        _verifier, challenge = _pkce_pair()
        redirect_uri = "http://127.0.0.1:9/callback"
        client = self._authorize_client(redirect_uri=redirect_uri, name="Denied Client")
        params = self._authorize_params(client, challenge, redirect_uri=redirect_uri)

        page = self.url_open("/mcp/oauth/authorize?" + urlencode(params))
        self.assertEqual(page.status_code, 200, page.text)

        consent = self.url_open(
            "/mcp/oauth/authorize",
            data={
                "csrf_token": self._csrf_token(page),
                "client_id": params["client_id"],
                "redirect_uri": redirect_uri,
                "state": params["state"],
                "scope": params["scope"],
                "resource": params["resource"],
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "response_type": "code",
                "deny": "1",
            },
            allow_redirects=False,
        )
        self.assertIn(consent.status_code, (301, 302, 303, 307), consent.text)
        location = consent.headers.get("Location", "")
        query = parse_qs(urlparse(location).query)
        self.assertEqual(query.get("error"), ["access_denied"])
        self.assertEqual(query.get("state"), [params["state"]])
        self.assertNotIn("code", query)

    def test_authorize_https_chatgpt_style_redirect_uri(self):
        self.authenticate("admin", "admin")
        _verifier, challenge = _pkce_pair()
        redirect_uri = "https://chatgpt.com/connector/oauth/TcOKfq1os1i5"
        client = self._authorize_client(
            redirect_uri=redirect_uri, name="ChatGPT HTTPS Client"
        )
        params = self._authorize_params(client, challenge, redirect_uri=redirect_uri)
        page = self.url_open("/mcp/oauth/authorize?" + urlencode(params))
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn(b"o_mcp_oauth_authorize", page.content)

        consent = self.url_open(
            "/mcp/oauth/authorize",
            data={
                "csrf_token": self._csrf_token(page),
                "client_id": params["client_id"],
                "redirect_uri": redirect_uri,
                "state": params["state"],
                "scope": params["scope"],
                "resource": params["resource"],
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "response_type": "code",
                "allow": "1",
            },
            allow_redirects=False,
        )
        self.assertIn(consent.status_code, (301, 302, 303, 307), consent.text)
        location = consent.headers.get("Location", "")
        self.assertTrue(location.startswith(redirect_uri), location)
        self.assertTrue(parse_qs(urlparse(location).query).get("code"))
