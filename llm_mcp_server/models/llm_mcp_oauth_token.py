from odoo import api, fields, models

from ..oauth import (
    DEFAULT_ACCESS_TOKEN_SECONDS,
    DEFAULT_AUTH_CODE_SECONDS,
    DEFAULT_REFRESH_TOKEN_SECONDS,
    MCP_OAUTH_SCOPE,
    expiry_datetime,
    is_expired,
    new_token,
    resource_uris_match,
    verify_pkce,
)


class LLMMCPOauthAuthorizationCode(models.Model):
    _name = "llm.mcp.oauth.authorization.code"
    _description = "MCP OAuth Authorization Code"
    _order = "create_date desc"

    code = fields.Char(required=True, index=True, copy=False)
    client_id = fields.Many2one("llm.mcp.oauth.client", required=True, ondelete="cascade")
    user_id = fields.Many2one("res.users", required=True, ondelete="cascade")
    redirect_uri = fields.Char(required=True)
    code_challenge = fields.Char(required=True)
    code_challenge_method = fields.Char(default="S256", required=True)
    resource = fields.Char(required=True)
    scope = fields.Char(default=MCP_OAUTH_SCOPE)
    expires = fields.Datetime(required=True)
    consumed = fields.Boolean(default=False)

    def is_valid(self, client, redirect_uri, code_verifier, resource):
        self.ensure_one()
        if self.consumed or is_expired(self.expires):
            return False
        if self.client_id != client:
            return False
        if self.redirect_uri != redirect_uri:
            return False
        if not resource_uris_match(self.resource, resource):
            return False
        return verify_pkce(code_verifier, self.code_challenge, self.code_challenge_method)

    @api.model
    def issue(self, client, user, redirect_uri, code_challenge, method, resource, scope):
        return self.sudo().create(
            {
                "code": new_token(24),
                "client_id": client.id,
                "user_id": user.id,
                "redirect_uri": redirect_uri,
                "code_challenge": code_challenge,
                "code_challenge_method": (method or "S256").upper(),
                "resource": resource,
                "scope": scope or MCP_OAUTH_SCOPE,
                "expires": expiry_datetime(DEFAULT_AUTH_CODE_SECONDS),
            }
        )


class LLMMCPOauthToken(models.Model):
    _name = "llm.mcp.oauth.token"
    _description = "MCP OAuth Token"
    _order = "create_date desc"

    access_token = fields.Char(required=True, index=True, copy=False)
    refresh_token = fields.Char(index=True, copy=False)
    client_id = fields.Many2one("llm.mcp.oauth.client", required=True, ondelete="cascade")
    user_id = fields.Many2one("res.users", required=True, ondelete="cascade")
    resource = fields.Char(required=True)
    scope = fields.Char(default=MCP_OAUTH_SCOPE)
    expires = fields.Datetime(required=True)
    refresh_expires = fields.Datetime()
    revoked = fields.Boolean(default=False)

    def is_access_valid(self, resource=None):
        self.ensure_one()
        if self.revoked or is_expired(self.expires):
            return False
        if resource and not resource_uris_match(self.resource, resource):
            return False
        return True

    def token_response(self):
        self.ensure_one()
        now = fields.Datetime.now()
        expires_in = max(int((self.expires - now).total_seconds()), 0)
        payload = {
            "access_token": self.access_token,
            "token_type": "Bearer",
            "expires_in": expires_in,
            "scope": self.scope or MCP_OAUTH_SCOPE,
            "resource": self.resource,
        }
        if self.refresh_token:
            payload["refresh_token"] = self.refresh_token
        return payload

    def revoke(self):
        self.write({"revoked": True})

    @api.model
    def issue(self, client, user, resource, scope, with_refresh=True):
        values = {
            "access_token": new_token(32),
            "client_id": client.id,
            "user_id": user.id,
            "resource": resource,
            "scope": scope or MCP_OAUTH_SCOPE,
            "expires": expiry_datetime(DEFAULT_ACCESS_TOKEN_SECONDS),
        }
        if with_refresh:
            values["refresh_token"] = new_token(32)
            values["refresh_expires"] = expiry_datetime(DEFAULT_REFRESH_TOKEN_SECONDS)
        return self.sudo().create(values)
