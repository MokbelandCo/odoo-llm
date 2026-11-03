from odoo import api, models
from odoo.exceptions import AccessError

from .common import AUTHORIZATION_GUARD


class LLMKnowledgeChunk(models.Model):
    _inherit = "llm.knowledge.chunk"

    def _llm_authorized_chunks(self):
        if not self or self.env.su:
            return self
        resources = self.mapped("resource_id")._llm_authorized_records()
        return self.filtered(lambda chunk: chunk.resource_id in resources)

    def _llm_check_source_access(self):
        if not self.env.su and set(self._llm_authorized_chunks().ids) != set(self.ids):
            raise AccessError("You do not have access to this knowledge content.")
        return True

    def read(self, fields=None, load="_classic_read"):
        self._llm_check_source_access()
        return super().read(fields=fields, load=load)

    def export_data(self, fields_to_export):
        self._llm_check_source_access()
        return super().export_data(fields_to_export)

    def _fetch_query(self, query, fields):
        records = super()._fetch_query(query, fields)
        if self.env.context.get("_llm_authorization_guard") is not AUTHORIZATION_GUARD:
            records.with_context(
                _llm_authorization_guard=AUTHORIZATION_GUARD
            )._llm_check_source_access()
        return records

    @api.model
    def llm_secure_semantic_search(  # noqa: C901
        self,
        query,
        collection,
        query_vector=None,
        resource_limit=5,
        chunks_per_resource=1,
        similarity_cutoff=0.5,
        domain=None,
        source_model=None,
        source_domain=None,
        source_options=None,
        max_candidates=None,
    ):
        """Retrieve authorized resources with exact missing-count candidate batches."""
        collection = collection.exists()
        if not collection:
            raise AccessError("Knowledge collection not found.")
        collection._llm_check_collection_access()
        resource_limit = max(1, min(int(resource_limit or 5), 100))
        chunks_per_resource = max(1, min(int(chunks_per_resource or 1), 20))
        if max_candidates is None:
            value = self.env["ir.config_parameter"].sudo().get_param(
                "llm_knowledge_security.max_candidates", "500"
            )
            max_candidates = max(resource_limit, int(value))

        if query_vector is None:
            query_vector = collection.sudo().embedding_model_id.embedding(
                query.strip()
            )[0]
        retained = {}
        retained_order = []
        examined_chunk_ids = set()
        vector_offset = 0
        exhausted = False

        while len(retained_order) < resource_limit and vector_offset < max_candidates:
            missing = resource_limit - len(retained_order)
            candidate_limit = min(missing, max_candidates - vector_offset)
            raw = collection.sudo().search_vectors(
                query_vector=query_vector,
                limit=candidate_limit,
                offset=vector_offset,
                min_similarity=similarity_cutoff,
            )
            vector_offset += len(raw)
            if not raw:
                exhausted = True
                break

            ordered_ids = [
                int(item["id"])
                for item in raw
                if item.get("id") and int(item["id"]) not in examined_chunk_ids
            ]
            examined_chunk_ids.update(ordered_ids)
            if not ordered_ids:
                if len(raw) < candidate_limit:
                    exhausted = True
                    break
                continue

            candidate_domain = [("id", "in", ordered_ids)]
            if domain:
                candidate_domain += list(domain)
            # Candidate IDs remain internal until both ORM domain and live source checks pass.
            guarded = self.with_context(
                _llm_authorization_guard=AUTHORIZATION_GUARD
            )
            candidates = super(LLMKnowledgeChunk, guarded).search_fetch(
                candidate_domain, []
            )
            candidates = candidates._llm_authorized_chunks()
            if source_model and source_domain is not None:
                candidate_source_ids = candidates.filtered(
                    lambda item: item.resource_id.res_model == source_model
                ).mapped("resource_id.res_id")
                source_records = (
                    self.env[source_model]
                    .with_context(**(source_options or {}))
                    .search(
                        list(source_domain)
                        + [("id", "in", candidate_source_ids or [0])]
                    )
                )
                if hasattr(source_records, "llm_knowledge_candidate_allowed"):
                    source_records = source_records.filtered(
                        lambda record: record.llm_knowledge_candidate_allowed()
                    )
                source_ids = set(source_records.ids)
                candidates = candidates.filtered(
                    lambda item, allowed_ids=source_ids: (
                        item.resource_id.res_model == source_model
                        and item.resource_id.res_id in allowed_ids
                    )
                )
            by_id = {chunk.id: chunk for chunk in candidates}
            score_by_id = {
                int(item["id"]): float(item.get("score", 0.0)) for item in raw
            }
            for chunk_id in ordered_ids:
                chunk = by_id.get(chunk_id)
                if not chunk:
                    continue
                resource_id = chunk.resource_id.id
                if resource_id not in retained:
                    retained[resource_id] = []
                    retained_order.append(resource_id)
                if len(retained[resource_id]) < chunks_per_resource:
                    retained[resource_id].append(
                        chunk.with_context(
                            similarity_scores={chunk.id: score_by_id[chunk.id]}
                        )
                    )
            if len(raw) < candidate_limit:
                exhausted = True
                break

        results = []
        for resource_id in retained_order[:resource_limit]:
            for chunk in retained[resource_id]:
                results.append(
                    {
                        "chunk": chunk,
                        "score": chunk.env.context["similarity_scores"][chunk.id],
                    }
                )
        return {
            "results": results,
            "incomplete": not exhausted
            and len(retained_order) < resource_limit
            and vector_offset >= max_candidates,
            "examined_candidates": vector_offset,
        }

    @api.model
    def search(self, domain, offset=0, limit=None, order=None, **kwargs):
        guarded = self.with_context(_llm_authorization_guard=AUTHORIZATION_GUARD)
        has_vector_search = self._has_vector_search(
            domain,
            kwargs.get("vector_search_term"),
            kwargs.get("query_vector"),
        )
        if self.env.su and not has_vector_search:
            return super(LLMKnowledgeChunk, guarded).search_fetch(
                domain, [], offset=offset, limit=limit, order=order
            )
        if has_vector_search:
            query, filtered_domain = self._parse_vector_search_domain(domain)
            query = kwargs.get("vector_search_term", query)
            query_vector = kwargs.get("query_vector")
            collection_id = kwargs.get("collection_id")
            if query_vector and not collection_id:
                raise AccessError(
                    "A pre-computed vector requires a specific authorized collection."
                )
            collections = (
                self.env["llm.knowledge.collection"].browse(collection_id)
                if collection_id
                else self._get_vector_search_collections(query, None, None)
            )
            secured_results = []
            for collection in collections:
                secured = self.llm_secure_semantic_search(
                    query,
                    collection,
                    query_vector=query_vector,
                    resource_limit=limit or 100,
                    similarity_cutoff=kwargs.get("query_min_similarity", 0.5),
                    domain=filtered_domain,
                )
                secured_results.extend(secured["results"])
            secured_results.sort(
                key=lambda item: (-item["score"], item["chunk"].id)
            )
            result_items = secured_results[: limit or None]
            chunks = self.browse(
                [item["chunk"].id for item in result_items]
            ).with_context(
                similarity_scores={
                    item["chunk"].id: item["score"] for item in result_items
                }
            )
            return chunks[offset:]
        candidates = super(LLMKnowledgeChunk, guarded).search_fetch(
            domain, [], order=order
        )
        authorized = candidates._llm_authorized_chunks()
        end = offset + limit if limit else None
        return authorized[offset:end]

    @api.model
    def search_count(self, domain, limit=None):
        return len(self.search(domain, limit=limit))

    @api.model
    def search_fetch(
        self,
        domain,
        field_names=None,
        offset=0,
        limit=None,
        order=None,
    ):
        records = self.search(domain, offset=offset, limit=limit, order=order)
        records.fetch(field_names)
        return records

    @api.model
    def name_search(self, name="", domain=None, operator="ilike", limit=100):
        values = super().name_search(name, domain=domain, operator=operator, limit=limit)
        allowed_ids = set(self.search([("id", "in", [item[0] for item in values])]).ids)
        return [item for item in values if item[0] in allowed_ids]

    @api.model
    def _read_group(
        self,
        domain,
        groupby=(),
        aggregates=(),
        having=(),
        offset=0,
        limit=None,
        order=None,
    ):
        if not self.env.su:
            domain = list(domain) + [("id", "in", self.search(domain).ids or [0])]
        return super()._read_group(
            domain,
            groupby,
            aggregates,
            having,
            offset,
            limit,
            order,
        )