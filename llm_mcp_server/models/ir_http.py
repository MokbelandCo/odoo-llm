import logging

from werkzeug.datastructures import WWWAuthenticate
from werkzeug.exceptions import Unauthorized

from odoo import models
from odoo.http import request

from ..oauth import canonical_resource_uri

_logger = logging.getLogger(__name__)


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _mcp_unauthorized(cls, message, error="invalid_token"):
        config = request.env["llm.mcp.server.config"].sudo().get_active_config()
        challenge = WWWAuthenticate(
            "bearer",
            {
                "realm": "mcp",
                "resource_metadata": config.get_resource_metadata_url(),
                "error": error,
                "error_description": (message or "")[:200],
            },
        )
        raise Unauthorized(message, www_authenticate=challenge)

    @classmethod
    def _mcp_authenticate_oauth_token(cls, token_value):
        config = request.env["llm.mcp.server.config"].sudo().get_active_config()
        if not config.oauth_enabled:
            return None
        token = (
            request.env["llm.mcp.oauth.token"]
            .sudo()
            .search([("access_token", "=", token_value), ("revoked", "=", False)], limit=1)
        )
        if not token:
            return None
        expected_resource = canonical_resource_uri(config.get_mcp_server_url())
        if not token.is_access_valid(expected_resource):
            cls._mcp_unauthorized("OAuth access token is invalid, expired, or not for this MCP server")
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

        config = request.env["llm.mcp.server.config"].sudo().get_active_config()

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
