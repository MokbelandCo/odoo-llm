from odoo.tests import TransactionCase, tagged


@tagged("llm_knowledge_project", "post_install", "-at_install")
class TestProjectKnowledge(TransactionCase):
    def test_standard_project_install_and_queue_deduplication(self):
        project = self.env["project.project"].create({"name": "Standard Project"})
        task = self.env["project.task"].create(
            {
                "name": "Standard task",
                "description": "<p>Standard description</p>",
                "project_id": project.id,
            }
        )
        task.write({"name": "Standard task updated"})
        jobs = self.env["llm.knowledge.sync.job"].search(
            [
                ("model_id.model", "=", task._name),
                ("res_id", "=", task.id),
                ("state", "=", "pending"),
            ]
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs.collection_key, project._llm_collection_key())
        self.assertNotIn(
            "internal_description",
            task.llm_knowledge_index_fields(),
        )

    def test_project_move_queues_new_membership_without_duplicate_identity(self):
        first = self.env["project.project"].create({"name": "First Project"})
        second = self.env["project.project"].create({"name": "Second Project"})
        task = self.env["project.task"].create(
            {"name": "Moving task", "project_id": first.id}
        )
        task.project_id = second
        jobs = self.env["llm.knowledge.sync.job"].search(
            [
                ("model_id.model", "=", task._name),
                ("res_id", "=", task.id),
            ]
        )
        self.assertEqual(
            set(jobs.mapped("collection_key")),
            {first._llm_collection_key(), second._llm_collection_key()},
        )