from odoo import api, models
from odoo.tools import html2plaintext


class ProjectTask(models.Model):
    _inherit = "project.task"

    def _llm_collection_key(self):
        self.ensure_one()
        return (
            self.project_id._llm_collection_key()
            if self.project_id
            else "project.task:unassigned"
        )

    def llm_knowledge_index_fields(self, security_scope="default"):
        return [
            "name",
            "description",
            "project_id",
            "stage_id",
            "tag_ids",
            "user_ids",
        ]

    def llm_knowledge_resource_allowed(self, resource):
        self.ensure_one()
        return resource.security_scope == "default"

    def llm_get_fields(self, record=None):
        self.ensure_one()
        parts = [f"Task: {self.name}"]
        if self.description:
            parts.append(f"Description: {html2plaintext(self.description)}")
        if self.project_id:
            parts.append(f"Project: {self.project_id.display_name}")
        if self.stage_id:
            parts.append(f"Stage: {self.stage_id.display_name}")
        if self.tag_ids:
            parts.append(f"Tags: {', '.join(self.tag_ids.mapped('name'))}")
        if self.user_ids:
            parts.append(f"Assigned to: {', '.join(self.user_ids.mapped('name'))}")
        return [
            {
                "field_name": "description",
                "mimetype": "text/plain",
                "rawcontent": "\n".join(parts),
            }
        ]

    def _llm_enqueue_sync(self, operation="upsert"):
        jobs = self.env["llm.knowledge.sync.job"].sudo()
        for task in self:
            jobs.enqueue(
                task._name,
                task.id,
                task._llm_collection_key(),
                operation=operation,
            )

    @api.model_create_multi
    def create(self, vals_list):
        tasks = super().create(vals_list)
        tasks._llm_enqueue_sync()
        return tasks

    def write(self, values):
        result = super().write(values)
        indexed_fields = set(self.llm_knowledge_index_fields())
        if indexed_fields & set(values):
            self._llm_enqueue_sync()
        return result

    def llm_knowledge_sync(self, collection_key, operation):
        resource_model = self.env["llm.resource"].sudo()
        model_id = self.env["ir.model"]._get_id(self._name)
        for task in self.exists():
            resource = resource_model.search(
                [
                    ("model_id", "=", model_id),
                    ("res_id", "=", task.id),
                    ("security_scope", "=", "default"),
                ],
                limit=1,
            )
            collection = self.env["llm.knowledge.collection"]
            if task.project_id:
                task.project_id.llm_knowledge_sync(
                    task.project_id._llm_collection_key(), "upsert"
                )
                collection = task.project_id.llm_knowledge_collection_id.sudo()
            values = {
                "name": task.display_name,
                "model_id": model_id,
                "res_id": task.id,
                "security_scope": "default",
                "parser": "default",
                "collection_ids": [(6, 0, collection.ids)],
                "state": "draft",
            }
            if resource:
                resource.write(values)
            else:
                resource = resource_model.create(values)
            if operation == "upsert":
                resource.process_resource()
        return True

    def unlink(self):
        task_ids = self.ids
        model_id = self.env["ir.model"]._get_id(self._name)
        result = super().unlink()
        self.env["llm.resource"].sudo().search(
            [("model_id", "=", model_id), ("res_id", "in", task_ids)]
        ).unlink()
        return result

    @api.model
    def llm_semantic_search(
        self,
        query,
        project_id=None,
        limit=10,
        similarity_cutoff=0.5,
        exclude_ids=None,
    ):
        project_domain = [("llm_knowledge_collection_id", "!=", False)]
        if project_id:
            project_domain.append(("id", "=", project_id))
        projects = self.env["project.project"].search(project_domain)
        matches = {}
        chunk_model = self.env["llm.knowledge.chunk"]
        for project in projects:
            secured = chunk_model.llm_secure_semantic_search(
                query,
                project.llm_knowledge_collection_id,
                resource_limit=limit,
                similarity_cutoff=similarity_cutoff,
            )
            for item in secured["results"]:
                task = item["chunk"].resource_id
                if task.res_model != self._name or task.res_id in (exclude_ids or []):
                    continue
                matches[task.res_id] = max(matches.get(task.res_id, 0), item["score"])
        ordered = sorted(matches, key=lambda item: (-matches[item], item))[:limit]
        readable = self.search([("id", "in", ordered)])
        by_id = {task.id: task for task in readable}
        return [
            {"task": by_id[task_id], "score": matches[task_id]}
            for task_id in ordered
            if task_id in by_id
        ]

    def llm_find_similar(self, limit=10, similarity_cutoff=0.5):
        self.ensure_one()
        query = html2plaintext(self.description or self.name)
        return self.llm_semantic_search(
            query,
            project_id=self.project_id.id or None,
            limit=limit,
            similarity_cutoff=similarity_cutoff,
            exclude_ids=self.ids,
        )