from odoo import fields, models


class LlmMcpOauthClientSecretShow(models.AbstractModel):
    """Show a newly generated OAuth client secret. Plaintext is never stored."""

    _name = "llm.mcp.oauth.client.secret.show"
    _description = "Show MCP OAuth Client Secret"

    # Required for onchange that returns field values from context defaults
    id = fields.Id()
    client_id = fields.Char(readonly=True, string="Client ID")
    client_secret = fields.Char(readonly=True, string="Client Secret")
    token_endpoint_auth_method = fields.Selection(
        [
            ("none", "None (public / PKCE)"),
            ("client_secret_post", "Client Secret Post"),
            ("client_secret_basic", "Client Secret Basic"),
        ],
        readonly=True,
        string="Token Endpoint Auth Method",
    )
