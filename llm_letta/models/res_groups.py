from odoo import fields, models


class ResGroups(models.Model):
    """Odoo 18 stores API key lifetime on res.groups; Odoo 17 does not."""

    _inherit = "res.groups"

    api_key_duration = fields.Float(
        string="API Key Duration (days)",
        help="Suggested lifetime in days for Letta MCP API keys created for users in this group. "
        "Odoo 17 API keys do not expire; this is used only as a preferred duration hint.",
    )
