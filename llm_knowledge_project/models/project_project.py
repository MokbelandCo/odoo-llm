from odoo import api, fields, models


class ProjectProject(models.Model):
    _inherit = "project.project"

    llm_knowledge_collection_id = fields.Many2one(
        "llm.knowledge.collection",
        copy=False,
        readonly=True,
        groups="project.group_project_user",
    )

    def _llm_collection_key(self):
        self.ensure_one()
        return f"project.project:{self.id}"

    def _llm_enqueue_collection_sync(self):
        jobs = self.env["llm.knowledge.sync.job"].sudo()
        for project in self:
            jobs.enqueue(
                project._name,
                project.id,
                project._llm_collection_key(),
            )

    @api.model_create_multi
    def create(self, vals_list):
        projects = super().create(vals_list)
        projects._llm_enqueue_collection_sync()
        return projects

    def write(self, values):
        result = super().write(values)
        if {"name", "company_id", "active"} & set(values):
            self._llm_enqueue_collection_sync()
        return result

    def llm_knowledge_sync(self, collection_key, operation):
        for project in self.exists():
            collection = self.env[
                "llm.knowledge.collection"
            ].llm_get_or_create_managed(
                collection_key,
                project.display_name,
                company=project.company_id,
                source=project,
            )
            if collection.active != project.active:
                collection.active = project.active
            if project.llm_knowledge_collection_id != collection:
                project.sudo().llm_knowledge_collection_id = collection
        return True

    def unlink(self):
        collection_keys = [project._llm_collection_key() for project in self]
        result = super().unlink()
        self.env["llm.knowledge.collection"].sudo().search(
            [("managed_key", "in", collection_keys)]
        ).unlink()
        return result