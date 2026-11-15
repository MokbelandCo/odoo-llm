from collections import defaultdict

from odoo import api, models
from odoo.exceptions import AccessError

from .common import AUTHORIZATION_GUARD


class LLMResource(models.Model):
    _inherit = "llm.resource"

    def _llm_source_index_fields(self, source_record):
        if hasattr(source_record, "llm_knowledge_index_fields"):
            return list(source_record.llm_knowledge_index_fields(self.security_scope))
        return ["display_name"]

    def _llm_authorized_records(self):
        """Return resources whose collection and live source are readable now.

        Source checks are grouped by model and record-rule filtering is performed in
        one ORM query per model. No authorization decision is cached across requests.
        """
        if not self or self.env.su:
            return self
        by_model = defaultdict(lambda: self.env["llm.resource"])
        for resource in self:
            if resource.res_model and resource.res_id:
                by_model[resource.res_model] |= resource

        permitted = self.env["llm.resource"]
        for model_name, resources in by_model.items():
            if model_name not in self.env:
                continue
            source_model = self.env[model_name]
            try:
                source_model.check_access("read")
                readable = source_model.search(
                    [("id", "in", list(set(resources.mapped("res_id"))))]
                )
            except AccessError:
                continue
            readable_by_id = {record.id: record for record in readable}
            for resource in resources:
                source_record = readable_by_id.get(resource.res_id)
                if not source_record:
                    continue
                try:
                    field_names = resource._llm_source_index_fields(source_record)
                    for field_name in field_names:
                        source_model._check_field_access(
                            source_model._fields[field_name],
                            "read",
                        )
                    if (
                        hasattr(source_record, "llm_knowledge_resource_allowed")
                        and not source_record.llm_knowledge_resource_allowed(resource)
                    ):
                        continue
                    collections = resource.collection_ids
                    if not collections:
                        # Unassigned content is private to its owner.
                        if resource.owner_id == self.env.user:
                            permitted |= resource
                        continue
                    allowed_collections = collections.search(
                        [("id", "in", collections.ids)]
                    )
                    if allowed_collections:
                        permitted |= resource
                except (AccessError, KeyError):
                    continue
        return permitted

    def _llm_check_source_access(self):
        if self.env.su:
            return True
        if set(self._llm_authorized_records().ids) != set(self.ids):
            raise AccessError("You do not have access to this knowledge resource.")
        return True

    @api.model
    def search(self, domain, offset=0, limit=None, order=None):
        guarded = self.with_context(_llm_authorization_guard=AUTHORIZATION_GUARD)
        if self.env.su:
            return super(LLMResource, guarded).search_fetch(
                domain, [], offset=offset, limit=limit, order=order
            )
        candidates = super(LLMResource, guarded).search_fetch(
            domain, [], order=order
        )
        authorized = candidates._llm_authorized_records()
        end = offset + limit if limit else None
        return authorized[offset:end]

    @api.model
    def search_count(self, domain, limit=None):
        records = self.search(domain, limit=limit)
        return len(records)

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