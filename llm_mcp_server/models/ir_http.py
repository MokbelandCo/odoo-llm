import logging
import re

from werkzeug.datastructures import WWWAuthenticate
from werkzeug.exceptions import Unauthorized

from odoo import models
from odoo.exceptions import AccessDenied
from odoo.http import request

from ..oauth import canonical_resource_uri

_logger = logging.getLogger(__name__)


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _auth_method_bearer(cls):
        """Odoo 17 shim matching core Odoo 19 ``_auth_method_bearer``.

        Odoo 17 has no bearer API-key auth method. Keep this name so MCP
        can call the same ``_auth_method_bearer`` as on the 19.0 branch.
        Do not copy this shim onto 19.0 (core already defines it). If this
        17 module is loaded on Odoo 18+, defer to core.
        """
        parent_method = getattr(super(), "_auth_method_bearer", None)
        if callable(parent_method):
            return parent_method()

        headers = request.httprequest.headers

        def get_http_authorization_bearer_token():
            header = headers.get("Authorization")
            if header and (m := re.match(r"^bearer\s+(.+)$", header, re.IGNORECASE)):
                return m.group(1)
            return None

        def check_sec_headers():
            return (
                headers.get("Sec-Fetch-Dest") == "document"
                and headers.get("Sec-Fetch-Mode") == "navigate"
                and headers.get("Sec-Fetch-Site") in ("none", "same-origin")
                and headers.get("Sec-Fetch-User") == "?1"
            )

        if token := get_http_authorization_bearer_token():
            uid = request.env["res.users.apikeys"]._check_credentials(
                scope="rpc", key=token
            )
            if not uid:
                raise Unauthorized(
                    "Invalid apikey",
                    www_authenticate=WWWAuthenticate("bearer"),
                )
            if request.env.uid and request.env.uid != uid:
                raise AccessDenied("Session user does not match the used apikey.")
            request.update_env(user=uid)
            request.session.can_save = False
        elif not request.env.uid:
            raise Unauthorized(
                "User not authenticated, use an API Key with a Bearer Authorization header.",
                www_authenticate=WWWAuthenticate("bearer"),
            )
        elif not check_sec_headers():
            raise Unauthorized(
                'Missing "Authorization" or Sec-headers for interactive usage.',
                www_authenticate=WWWAuthenticate("bearer"),
            )
        cls._auth_method_user()

    @classmethod
    def _mcp_unauthorized(cls, message, error="invalid_token"):
        config = request.env["llm.mcp.server.config"].sudo().get_config_for_request()
        challenge_parameters = {
            "realm": "mcp",
            "error": error,
            "error_description": (message or "")[:200],
        }
        if config.oauth_enabled:
            challenge_parameters["resource_metadata"] = (
                config.get_resource_metadata_url()
            )
        challenge = WWWAuthenticate("bearer", challenge_parameters)
        raise Unauthorized(message, www_authenticate=challenge)

    @classmethod
    def _mcp_authenticate_oauth_token(cls, token_value):
        config = request.env["llm.mcp.server.config"].sudo().get_config_for_request()
        if not config.oauth_enabled:
            return None
        token = (
            request.env["llm.mcp.oauth.token"]
            .sudo()
            .search(
                [("access_token", "=", token_value), ("revoked", "=", False)], limit=1
            )
        )
        if not token:
            return None
        expected_resource = canonical_resource_uri(config.get_mcp_server_url())
        if not token.is_access_valid(expected_resource):
            cls._mcp_unauthorized(
                "OAuth access token is invalid, expired, or not for this MCP server"
            )
        if not token.user_id or not token.user_id.active:
            cls._mcp_unauthorized("OAuth token user is inactive")
        request.update_env(user=token.user_id.id)
        request.session.can_save = False
        return token.user_id.id

    @classmethod
    def _auth_method_mcp_bearer(cls):
        """Bearer auth that accepts MCP OAuth tokens, then Odoo API keys."""
        request.update_env(user=False)
        headers = request.httprequest.headers
        header = headers.get("Authorization") or ""
        token_value = None
        if header.lower().startswith("bearer "):
            token_value = header[7:].strip()

        config = request.env["llm.mcp.server.config"].sudo().get_config_for_request()

        if not token_value:
            cls._mcp_unauthorized(
                "Missing Bearer token",
                error="invalid_request",
            )

        oauth_uid = cls._mcp_authenticate_oauth_token(token_value)
        if oauth_uid:
            cls._auth_method_user()
            return

        if not config.allow_api_key:
            cls._mcp_unauthorized("API keys are disabled; use an OAuth access token")

        try:
            request.env["ir.http"]._auth_method_bearer()
        except Unauthorized as exc:
            cls._mcp_unauthorized(exc.description or "Invalid Bearer token")
