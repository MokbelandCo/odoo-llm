from odoo import models


class ResUsers(models.Model):
    _inherit = "res.users"

    def _init_messaging(self):
        """Include LLM threads in Odoo 17 init_messaging payload."""
        values = super()._init_messaging()
        llm_threads = self.env["llm.thread"].search(
            [("user_id", "=", self.id), ("active", "=", True)],
            order="write_date DESC",
        )
        values["llm_threads"] = [thread._llm_thread_format() for thread in llm_threads]
        return values
