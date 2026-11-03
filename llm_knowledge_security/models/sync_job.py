import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class LLMKnowledgeSyncJob(models.Model):
    _name = "llm.knowledge.sync.job"
    _description = "LLM Knowledge Synchronization Job"
    _order = "create_date, id"

    model_id = fields.Many2one("ir.model", required=True, ondelete="cascade", index=True)
    res_id = fields.Integer(required=True, index=True)
    collection_key = fields.Char(required=True, index=True)
    operation = fields.Selection(
        [("upsert", "Upsert"), ("delete", "Delete")],
        required=True,
        default="upsert",
    )
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("running", "Running"),
            ("done", "Done"),
            ("failed", "Failed"),
        ],
        default="pending",
        required=True,
        index=True,
    )
    attempts = fields.Integer(default=0)
    last_error = fields.Char(readonly=True)

    @api.model
    def enqueue(self, model_name, res_id, collection_key, operation="upsert"):
        model_id = self.env["ir.model"]._get_id(model_name)
        existing = self.search(
            [
                ("model_id", "=", model_id),
                ("res_id", "=", res_id),
                ("collection_key", "=", collection_key),
                ("state", "in", ["pending", "running", "failed"]),
            ],
            limit=1,
        )
        if existing:
            values = {}
            if existing.operation != operation and existing.state != "running":
                values["operation"] = operation
            if existing.state == "failed" and existing.attempts < 5:
                values["state"] = "pending"
            if values:
                existing.write(values)
            return existing
        return self.create(
            {
                "model_id": model_id,
                "res_id": res_id,
                "collection_key": collection_key,
                "operation": operation,
            }
        )

    @api.model
    def _cron_process(self, batch_size=50):
        jobs = self.search([("state", "in", ["pending", "failed"])], limit=batch_size)
        for job in jobs:
            if job.attempts >= 5:
                continue
            job.write({"state": "running", "attempts": job.attempts + 1})
            try:
                model_name = job.model_id.model
                source = self.env[model_name].browse(job.res_id)
                if hasattr(source, "llm_knowledge_sync"):
                    source.llm_knowledge_sync(job.collection_key, job.operation)
                job.write({"state": "done", "last_error": False})
            except Exception as error:
                # Never copy source content into operational errors.
                _logger.exception(
                    "Knowledge sync failed for %s/%s",
                    job.model_id.model,
                    job.res_id,
                )
                job.write({"state": "failed", "last_error": type(error).__name__})