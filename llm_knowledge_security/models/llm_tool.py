from odoo import api, models


class LLMTool(models.Model):
    _inherit = "llm.tool"

    @api.model
    def _get_available_collections(self):
        values = self.env["llm.knowledge.collection"].llm_discover(limit=100)
        return [(str(value["id"]), value["name"]) for value in values]