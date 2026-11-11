from odoo import _, models

from odoo.addons.llm_tool.decorators import llm_tool
from odoo.addons.llm_tool.record_reference import record_reference


class LLMDummyTools(models.AbstractModel):
    """Synthetic tools for exercising chat features with the dummy provider."""

    _name = "llm.dummy.tools"
    _description = "LLM Dummy Tools"

    @llm_tool(
        read_only_hint=True,
        idempotent_hint=True,
        destructive_hint=False,
        open_world_hint=False,
    )
    def llm_dummy_find_contact(self, name: str = "") -> dict:
        """Find a contact by name and return a record reference to it.

        Without a name, returns the current user's own contact. Used to test
        chat record links; it only reads contacts the current user can read.
        """
        if name:
            partner = self.env["res.partner"].search([("name", "ilike", name)], limit=1)
        else:
            partner = self.env.user.partner_id
        if not partner:
            return {"found": False, "message": _("No contact matches %s.", name)}
        return {"found": True, "contact": record_reference(partner)}
