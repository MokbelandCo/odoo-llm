from odoo import api, fields, models
from odoo.exceptions import ValidationError

from ..oauth import (
    MCP_OAUTH_SCOPE,
    hash_secret,
    is_safe_redirect_uri,
    new_token,
    secrets_match,
)


class LLMMCPOauthClient(models.Model):
    _name = "llm.mcp.oauth.client"
    _description = "MCP OAuth Client"
    _order = "create_date desc"

    name = fields.Char(required=True, default="MCP Client")
    client_id = fields.Char(
        required=True, index=True, copy=False, default=lambda self: new_token(16)
    )
    client_secret_hash = fields.Char(copy=False)
    is_confidential = fields.Boolean(default=False)
    token_endpoint_auth_method = fields.Selection(
        [
            ("none", "None (public / PKCE)"),
            ("client_secret_post", "Client Secret Post"),
            ("client_secret_basic", "Client Secret Basic"),
        ],
        default="none",
        required=True,
    )
    redirect_uris = fields.Json(default=list)
    grant_types = fields.Json(
        default=lambda self: ["authorization_code", "refresh_token"]
    )
    response_types = fields.Json(default=lambda self: ["code"])
    scope = fields.Char(default=MCP_OAUTH_SCOPE)
    service_user_id = fields.Many2one(
        "res.users",
        help="Odoo user used for the client_credentials grant. Required for that grant.",
    )
    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            "client_id_unique",
            "unique(client_id)",
            "OAuth client_id must be unique.",
        ),
    ]

    @api.constrains("redirect_uris")
    def _check_redirect_uris(self):
        for client in self:
            for uri in client.redirect_uris or []:
                if not is_safe_redirect_uri(uri):
                    raise ValidationError(
                        "Redirect URI must be HTTPS or localhost HTTP without a fragment."
                    )

    def set_client_secret(self, secret):
        self.ensure_one()
        self.client_secret_hash = hash_secret(secret) if secret else False
        self.is_confidential = bool(secret)
        if secret and self.token_endpoint_auth_method == "none":
            self.token_endpoint_auth_method = "client_secret_post"

    def check_secret(self, secret):
        self.ensure_one()
        if not self.is_confidential:
            return not secret
        return secrets_match(self.client_secret_hash, secret or "")

    def allows_redirect(self, redirect_uri):
        self.ensure_one()
        return redirect_uri in (self.redirect_uris or [])

    def allows_grant(self, grant_type):
        self.ensure_one()
        return grant_type in (self.grant_types or [])

    def registration_payload(self, client_secret=None):
        self.ensure_one()
        payload = {
            "client_id": self.client_id,
            "client_name": self.name,
            "redirect_uris": self.redirect_uris or [],
            "grant_types": self.grant_types or [],
            "response_types": self.response_types or [],
            "token_endpoint_auth_method": self.token_endpoint_auth_method,
            "scope": self.scope or MCP_OAUTH_SCOPE,
            "client_id_issued_at": int(self.create_date.timestamp())
            if self.create_date
            else None,
        }
        if client_secret:
            payload["client_secret"] = client_secret
        return payload
