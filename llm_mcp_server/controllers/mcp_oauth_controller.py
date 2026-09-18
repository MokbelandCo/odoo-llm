"""MCP OAuth 2.1 authorization server endpoints (RFC 8414, 7591, 8707, 9728)."""

from werkzeug.exceptions import BadRequest

from odoo import http
from odoo.http import request

from ..oauth import (
    MCP_OAUTH_SCOPE,
    canonical_resource_uri,
    is_expired,
    is_safe_redirect_uri,
    new_token,
    parse_basic_client_auth,
    redirect_uri_with_params,
)

def _json_response(payload, status=200):
    return request.make_json_response(payload, status=status)


def _oauth_error(error, description, status=400):
    return _json_response({"error": error, "error_description": description}, status=status)


class MCPOAuthController(http.Controller):
    def _config(self, resource=None, endpoint_path=None):
        Config = request.env["llm.mcp.server.config"].sudo()
        resource = resource or request.params.get("resource")
        if resource:
            return Config.get_config_for_resource(resource)
        if endpoint_path:
            return Config.get_config_for_request(endpoint_path)
        return Config.get_active_config()

    def _cors_json(self, payload, status=200):
        response = _json_response(payload, status=status)
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = (
            "Authorization, Content-Type, Mcp-Session-Id, Mcp-Protocol-Version"
        )
        return response

    @http.route(
        [
            "/.well-known/oauth-protected-resource",
            "/.well-known/oauth-protected-resource/mcp",
            "/.well-known/oauth-protected-resource/mcp/<string:server_name>",
        ],
        type="http",
        auth="public",
        methods=["GET", "OPTIONS"],
        csrf=False,
        cors="*",
    )
    def protected_resource_metadata(self, server_name=None, **kwargs):
        endpoint_path = f"/mcp/{server_name}" if server_name else "/mcp"
        config = self._config(endpoint_path=endpoint_path)
        if not config.oauth_enabled:
            return self._cors_json(
                {"error": "oauth_disabled", "error_description": "OAuth is disabled"},
                status=404,
            )
        return self._cors_json(config.get_protected_resource_metadata())

    @http.route(
        [
            "/.well-known/oauth-authorization-server",
            "/.well-known/oauth-authorization-server/mcp",
        ],
        type="http",
        auth="public",
        methods=["GET", "OPTIONS"],
        csrf=False,
        cors="*",
    )
    def authorization_server_metadata(self, **kwargs):
        config = self._config()
        if not config.oauth_enabled:
            return self._cors_json(
                {"error": "oauth_disabled", "error_description": "OAuth is disabled"},
                status=404,
            )
        return self._cors_json(config.get_authorization_server_metadata())

    @http.route(
        "/mcp/oauth/register",
        type="http",
        auth="public",
        methods=["POST", "OPTIONS"],
        csrf=False,
        cors="*",
    )
    def register_client(self, **kwargs):
        config = self._config()
        if not config.oauth_enabled:
            return _oauth_error("invalid_request", "OAuth is disabled", status=404)
        try:
            data = request.get_json_data() or {}
        except (ValueError, AttributeError):
            data = dict(request.params)

        redirect_uris = data.get("redirect_uris") or []
        if isinstance(redirect_uris, str):
            redirect_uris = [redirect_uris]
        if not redirect_uris or not all(is_safe_redirect_uri(uri) for uri in redirect_uris):
            return _oauth_error(
                "invalid_redirect_uri",
                "redirect_uris is required and each URI must be HTTPS or localhost HTTP",
            )

        grant_types = data.get("grant_types") or ["authorization_code", "refresh_token"]
        auth_method = data.get("token_endpoint_auth_method") or "none"
        if auth_method not in ("none", "client_secret_post", "client_secret_basic"):
            return _oauth_error("invalid_client_metadata", "Unsupported token_endpoint_auth_method")

        client_secret = None
        confidential = auth_method != "none"
        values = {
            "name": data.get("client_name") or "Dynamically registered MCP client",
            "redirect_uris": redirect_uris,
            "grant_types": grant_types,
            "response_types": data.get("response_types") or ["code"],
            "token_endpoint_auth_method": auth_method,
            "scope": data.get("scope") or MCP_OAUTH_SCOPE,
            "is_confidential": confidential,
        }
        client = request.env["llm.mcp.oauth.client"].sudo().create(values)
        if confidential:
            client_secret = new_token(24)
            client.set_client_secret(client_secret)
        return _json_response(client.registration_payload(client_secret), status=201)

    def _get_authorize_params(self):
        params = request.params
        return {
            "client_id": params.get("client_id"),
            "redirect_uri": params.get("redirect_uri"),
            "response_type": params.get("response_type") or "code",
            "state": params.get("state"),
            "scope": params.get("scope") or MCP_OAUTH_SCOPE,
            "code_challenge": params.get("code_challenge"),
            "code_challenge_method": (params.get("code_challenge_method") or "S256").upper(),
            "resource": params.get("resource"),
        }

    def _authorize_error_redirect(self, redirect_uri, error, description, state=None):
        if not is_safe_redirect_uri(redirect_uri):
            return request.render(
                "llm_mcp_server.mcp_oauth_error",
                {"title": error, "message": description},
            )
        query = {"error": error, "error_description": description}
        if state:
            query["state"] = state
        return request.redirect(redirect_uri_with_params(redirect_uri, query), local=False)

    @http.route(
        "/mcp/oauth/authorize",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def authorize(self, **kwargs):
        config = self._config()
        if not config.oauth_enabled:
            return request.render(
                "llm_mcp_server.mcp_oauth_error",
                {"title": "OAuth disabled", "message": "MCP OAuth is not enabled."},
            )
        params = self._get_authorize_params()
        client = (
            request.env["llm.mcp.oauth.client"]
            .sudo()
            .search([("client_id", "=", params["client_id"]), ("active", "=", True)], limit=1)
        )
        if not client:
            return request.render(
                "llm_mcp_server.mcp_oauth_error",
                {"title": "Unknown client", "message": "client_id is not registered."},
            )
        if params["response_type"] != "code":
            return self._authorize_error_redirect(
                params["redirect_uri"],
                "unsupported_response_type",
                "Only response_type=code is supported",
                params["state"],
            )
        if not client.allows_redirect(params["redirect_uri"]):
            return request.render(
                "llm_mcp_server.mcp_oauth_error",
                {"title": "Invalid redirect", "message": "redirect_uri is not registered for this client."},
            )
        if params["code_challenge_method"] != "S256" or not params["code_challenge"]:
            return self._authorize_error_redirect(
                params["redirect_uri"],
                "invalid_request",
                "PKCE S256 code_challenge is required",
                params["state"],
            )
        resource = canonical_resource_uri(params["resource"] or config.get_mcp_server_url())
        expected = canonical_resource_uri(config.get_mcp_server_url())
        if resource != expected:
            return self._authorize_error_redirect(
                params["redirect_uri"],
                "invalid_target",
                "resource must be the MCP server canonical URI",
                params["state"],
            )
        return request.render(
            "llm_mcp_server.mcp_oauth_authorize",
            {
                "title": "Authorize MCP client",
                "client_name": client.name,
                "scope": params["scope"],
                "resource": resource,
                "client_id": client.client_id,
                "redirect_uri": params["redirect_uri"],
                "state": params["state"] or "",
                "code_challenge": params["code_challenge"],
                "code_challenge_method": params["code_challenge_method"],
                "response_type": "code",
            },
        )

    @http.route(
        "/mcp/oauth/authorize",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=True,
    )
    def authorize_post(self, **kwargs):
        config = self._config()
        params = self._get_authorize_params()
        if request.params.get("deny"):
            return self._authorize_error_redirect(
                params["redirect_uri"],
                "access_denied",
                "The user denied the request",
                params["state"],
            )
        client = (
            request.env["llm.mcp.oauth.client"]
            .sudo()
            .search([("client_id", "=", params["client_id"]), ("active", "=", True)], limit=1)
        )
        if not client or not client.allows_redirect(params["redirect_uri"]):
            raise BadRequest("Invalid client or redirect_uri")
        resource = canonical_resource_uri(params["resource"] or config.get_mcp_server_url())
        code = request.env["llm.mcp.oauth.authorization.code"].issue(
            client,
            request.env.user,
            params["redirect_uri"],
            params["code_challenge"],
            params["code_challenge_method"],
            resource,
            params["scope"],
        )
        query = {"code": code.code}
        if params["state"]:
            query["state"] = params["state"]
        return request.redirect(
            redirect_uri_with_params(params["redirect_uri"], query), local=False
        )

    def _extract_client(self):
        header = request.httprequest.headers.get("Authorization")
        basic_id, basic_secret = parse_basic_client_auth(header)
        params = dict(request.params)
        if request.httprequest.mimetype == "application/json":
            try:
                params.update(request.get_json_data() or {})
            except (ValueError, AttributeError):
                pass
        client_id = basic_id or params.get("client_id")
        client_secret = basic_secret if basic_id else params.get("client_secret")
        client = (
            request.env["llm.mcp.oauth.client"]
            .sudo()
            .search([("client_id", "=", client_id), ("active", "=", True)], limit=1)
            if client_id
            else request.env["llm.mcp.oauth.client"]
        )
        return client, client_secret, params

    @http.route(
        "/mcp/oauth/token",
        type="http",
        auth="public",
        methods=["POST", "OPTIONS"],
        csrf=False,
        cors="*",
    )
    def token(self, **kwargs):
        client, client_secret, params = self._extract_client()
        resource = params.get("resource")
        if not resource and params.get("grant_type") == "authorization_code":
            code = (
                request.env["llm.mcp.oauth.authorization.code"]
                .sudo()
                .search([("code", "=", params.get("code"))], limit=1)
            )
            resource = code.resource
        if not resource and params.get("grant_type") == "refresh_token":
            token = (
                request.env["llm.mcp.oauth.token"]
                .sudo()
                .search([("refresh_token", "=", params.get("refresh_token"))], limit=1)
            )
            resource = token.resource
        config = self._config(resource=resource)
        if not config.oauth_enabled:
            return _oauth_error("invalid_request", "OAuth is disabled", status=404)
        if not client:
            return _oauth_error("invalid_client", "Unknown client_id", status=401)
        if client.is_confidential and not client.check_secret(client_secret):
            return _oauth_error("invalid_client", "Invalid client secret", status=401)
        if not client.is_confidential and client_secret:
            return _oauth_error("invalid_client", "Public clients must not send a client_secret", status=401)

        grant_type = params.get("grant_type")
        if not client.allows_grant(grant_type):
            return _oauth_error("unauthorized_client", f"Grant {grant_type} is not allowed for this client")

        if grant_type == "authorization_code":
            return self._token_authorization_code(config, client, params)
        if grant_type == "refresh_token":
            return self._token_refresh(config, client, params)
        if grant_type == "client_credentials":
            return self._token_client_credentials(config, client, params)
        return _oauth_error("unsupported_grant_type", "Unsupported grant_type")

    def _token_authorization_code(self, config, client, params):
        code_value = params.get("code")
        redirect_uri = params.get("redirect_uri")
        code_verifier = params.get("code_verifier")
        resource = canonical_resource_uri(params.get("resource") or config.get_mcp_server_url())
        code = (
            request.env["llm.mcp.oauth.authorization.code"]
            .sudo()
            .search([("code", "=", code_value)], limit=1)
        )
        if not code or not code.is_valid(client, redirect_uri, code_verifier, resource):
            return _oauth_error("invalid_grant", "Authorization code is invalid, expired, or PKCE failed")
        code.consumed = True
        token = request.env["llm.mcp.oauth.token"].issue(
            client, code.user_id, resource, code.scope, with_refresh=True
        )
        return _json_response(token.token_response())

    def _token_refresh(self, config, client, params):
        refresh = params.get("refresh_token")
        token = (
            request.env["llm.mcp.oauth.token"]
            .sudo()
            .search(
                [
                    ("refresh_token", "=", refresh),
                    ("client_id", "=", client.id),
                    ("revoked", "=", False),
                ],
                limit=1,
            )
        )
        if not token or is_expired(token.refresh_expires):
            return _oauth_error("invalid_grant", "Refresh token is invalid or expired")
        resource = canonical_resource_uri(params.get("resource") or token.resource)
        expected = canonical_resource_uri(config.get_mcp_server_url())
        if resource != expected:
            return _oauth_error("invalid_target", "resource must be the MCP server canonical URI")
        token.revoke()
        new = request.env["llm.mcp.oauth.token"].issue(
            client, token.user_id, resource, token.scope, with_refresh=True
        )
        return _json_response(new.token_response())

    def _token_client_credentials(self, config, client, params):
        if not client.is_confidential:
            return _oauth_error("unauthorized_client", "client_credentials requires a confidential client")
        if not client.service_user_id:
            return _oauth_error("invalid_client", "Client is missing a service user")
        resource = canonical_resource_uri(params.get("resource") or config.get_mcp_server_url())
        expected = canonical_resource_uri(config.get_mcp_server_url())
        if resource != expected:
            return _oauth_error("invalid_target", "resource must be the MCP server canonical URI")
        token = request.env["llm.mcp.oauth.token"].issue(
            client,
            client.service_user_id,
            resource,
            params.get("scope") or client.scope or MCP_OAUTH_SCOPE,
            with_refresh=False,
        )
        return _json_response(token.token_response())

    @http.route(
        "/mcp/oauth/revoke",
        type="http",
        auth="public",
        methods=["POST", "OPTIONS"],
        csrf=False,
        cors="*",
    )
    def revoke(self, **kwargs):
        client, client_secret, params = self._extract_client()
        if client and client.is_confidential and not client.check_secret(client_secret):
            return _oauth_error("invalid_client", "Invalid client secret", status=401)
        token_value = params.get("token")
        if token_value:
            tokens = request.env["llm.mcp.oauth.token"].sudo().search(
                ["|", ("access_token", "=", token_value), ("refresh_token", "=", token_value)]
            )
            tokens.revoke()
        return request.make_json_response({}, status=200)

    @http.route(
        "/mcp/oauth/introspect",
        type="http",
        auth="public",
        methods=["POST", "OPTIONS"],
        csrf=False,
        cors="*",
    )
    def introspect(self, **kwargs):
        client, client_secret, params = self._extract_client()
        if not client or (client.is_confidential and not client.check_secret(client_secret)):
            return _oauth_error("invalid_client", "Invalid client", status=401)
        token = (
            request.env["llm.mcp.oauth.token"]
            .sudo()
            .search([("access_token", "=", params.get("token"))], limit=1)
        )
        if not token or not token.is_access_valid():
            return _json_response({"active": False})
        return _json_response(
            {
                "active": True,
                "scope": token.scope,
                "client_id": token.client_id.client_id,
                "username": token.user_id.login,
                "token_type": "Bearer",
                "exp": int(token.expires.timestamp()) if token.expires else None,
                "sub": str(token.user_id.id),
                "aud": token.resource,
            }
        )
