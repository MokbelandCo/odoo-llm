from unittest.mock import MagicMock

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged("llm_knowledge_security", "post_install", "-at_install")
class TestKnowledgeSecurity(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.internal_group = cls.env.ref("base.group_user")
        cls.owner_user = cls.env["res.users"].create(
            {
                "name": "Knowledge Owner User",
                "login": "knowledge-owner",
                "group_ids": [(6, 0, cls.internal_group.ids)],
            }
        )
        cls.other_user = cls.env["res.users"].create(
            {
                "name": "Knowledge Other User",
                "login": "knowledge-other",
                "group_ids": [(6, 0, cls.internal_group.ids)],
            }
        )
        cls.partner_model_id = cls.env["ir.model"]._get_id("res.partner")

    def _resource(self, source, owner=None):
        return self.env["llm.resource"].create(
            {
                "name": source.display_name,
                "model_id": self.partner_model_id,
                "res_id": source.id,
                "owner_id": (owner or self.env.user).id,
            }
        )

    def test_unassigned_resource_is_owner_private_and_deleted_source_denies(self):
        source = self.env["res.partner"].create({"name": "Private source"})
        resource = self._resource(source, owner=self.owner_user)
        self.assertEqual(
            resource.with_user(self.owner_user)._llm_authorized_records().ids,
            resource.ids,
        )
        self.assertFalse(resource.with_user(self.other_user)._llm_authorized_records())
        self.assertFalse(
            self.env["llm.resource"]
            .with_user(self.other_user)
            .search_fetch([("id", "=", resource.id)], ["name"])
        )
        source.unlink()
        with self.assertRaises(AccessError):
            resource.with_user(self.owner_user)._llm_authorized_records()

    def test_exact_missing_count_batches(self):
        sources = self.env["res.partner"].create(
            [{"name": f"Candidate {index}"} for index in range(65)]
        )
        resources = self.env["llm.resource"].create(
            [
                {
                    "name": source.display_name,
                    "model_id": self.partner_model_id,
                    "res_id": source.id,
                }
                for source in sources
            ]
        )
        chunks = self.env["llm.knowledge.chunk"].create(
            [
                {
                    "resource_id": resource.id,
                    "sequence": 10,
                    "content": f"candidate {index}",
                }
                for index, resource in enumerate(resources)
            ]
        )
        allowed = chunks[:38] | chunks[50:59] | chunks[62:65]
        calls = []

        collection = MagicMock()
        collection.exists.return_value = collection
        collection.sudo.return_value = collection
        collection.embedding_model_id.embedding.return_value = [[0.1, 0.2]]

        def search_vectors(**values):
            calls.append(values["limit"])
            start = values["offset"]
            stop = start + values["limit"]
            return [
                {"id": chunk.id, "score": 1.0 - index / 1000}
                for index, chunk in enumerate(chunks[start:stop], start=start)
            ]

        collection.search_vectors.side_effect = search_vectors
        result = self.env["llm.knowledge.chunk"].llm_secure_semantic_search(
            "query",
            collection,
            resource_limit=50,
            domain=[("id", "in", allowed.ids)],
            max_candidates=100,
        )
        self.assertEqual(calls, [50, 12, 3])
        self.assertEqual(len(result["results"]), 50)
        self.assertFalse(result["incomplete"])