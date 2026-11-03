from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.osv import expression

from .common import AUTHORIZATION_GUARD


class LLMKnowledgeCollection(models.Model):
    _inherit = "llm.knowledge.collection"

    allowed_group_ids = fields.Many2many(
        "res.groups",
        "llm_knowledge_collection_group_rel",
        "collection_id",
        "group_id",
        string="Allowed Groups",
        help="Users must belong to at least one selected group. Empty adds no group restriction.",
    )
    company_id = fields.Many2one(
        "res.company",
        index=True,
        help="Empty collections are shared, but remain subject to group and source access.",
    )
    managed_key = fields.Char(
        index="btree_not_null",
        copy=False,
        help="Stable integration identity. Do not derive this value from the collection name.",
    )
    source_model_id = fields.Many2one("ir.model", ondelete="cascade", index=True)
    source_res_id = fields.Integer(index=True)

    _managed_key_unique = models.Constraint(
        "UNIQUE(managed_key)",
        "A managed knowledge collection already exists for this source.",
    )

    @api.model
    def _llm_validate_company_context(self):
        requested = set(self.env.context.get("allowed_company_ids") or [])
        permitted = set(self.env.user.company_ids.ids)
        if requested - permitted:
            raise AccessError("The requested company context is not permitted.")
        return self.env.companies

    @api.model
    def _llm_collection_access_domain(self):
        companies = self._llm_validate_company_context()
        group_ids = self.env.user.all_group_ids.ids
        group_domain = [
            "|",
            ("allowed_group_ids", "=", False),
            ("allowed_group_ids", "in", group_ids or [0]),
        ]
        company_domain = [
            "|",
            ("company_id", "=", False),
            ("company_id", "in", companies.ids or [0]),
        ]
        return expression.AND([group_domain, company_domain])

    @api.model
    def _llm_security_bypass(self):
        # Context is caller-controlled over RPC and must never be an authorization bypass.
        return self.env.su

    @api.model
    def search(self, domain, offset=0, limit=None, order=None):
        guarded = self.with_context(_llm_authorization_guard=AUTHORIZATION_GUARD)
        if not self._llm_security_bypass():
            domain = expression.AND([domain, self._llm_collection_access_domain()])
            candidates = super(LLMKnowledgeCollection, guarded).search_fetch(
                domain, [], order=order
            )
            authorized = candidates._llm_authorized_collections()
            end = offset + limit if limit else None
            return authorized[offset:end]
        return super(LLMKnowledgeCollection, guarded).search_fetch(
            domain, [], offset=offset, limit=limit, order=order
        )

    @api.model
    def search_count(self, domain, limit=None):
        if self._llm_security_bypass():
            return super().search_count(domain, limit=limit)
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

    def _llm_authorized_collections(self):
        if not self or self._llm_security_bypass():
            return self
        permitted = self.env["llm.knowledge.collection"]
        by_model = {}
        for collection in self:
            if not collection.source_model_id or not collection.source_res_id:
                permitted |= collection
                continue
            by_model.setdefault(collection.source_model_id.model, []).append(collection)
        for model_name, collections in by_model.items():
            if model_name not in self.env:
                continue
            try:
                records = self.env[model_name].search(
                    [("id", "in", [item.source_res_id for item in collections])]
                )
            except AccessError:
                continue
            readable_ids = set(records.ids)
            for collection in collections:
                if collection.source_res_id in readable_ids:
                    permitted |= collection
        return permitted

    def _llm_check_collection_access(self):
        if self._llm_security_bypass():
            return True
        allowed = self.search([("id", "in", self.ids)])
        if set(allowed.ids) != set(self.ids):
            raise AccessError("You do not have access to this knowledge collection.")
        return True

    def search_vectors(self, query_vector, limit=10, filter=None, **kwargs):
        self.ensure_one()
        if self.env.su:
            return super().search_vectors(
                query_vector,
                limit=limit,
                filter=filter,
                **kwargs,
            )
        self._llm_check_collection_access()
        offset = max(0, int(kwargs.get("offset", 0)))
        secured = self.env[
            "llm.knowledge.chunk"
        ].llm_secure_semantic_search(
            query=None,
            query_vector=query_vector,
            collection=self,
            resource_limit=offset + limit,
            similarity_cutoff=kwargs.get("min_similarity", 0.5),
            domain=filter if isinstance(filter, (list, tuple)) else None,
        )
        return [
            {
                "id": item["chunk"].id,
                "score": item["score"],
                "metadata": {},
            }
            for item in secured["results"][offset : offset + limit]
        ]

    def _fetch_query(self, query, fields):
        records = super()._fetch_query(query, fields)
        if self.env.context.get("_llm_authorization_guard") is not AUTHORIZATION_GUARD:
            records.with_context(
                _llm_authorization_guard=AUTHORIZATION_GUARD
            )._llm_check_collection_access()
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

    @api.constrains("company_id")
    def _check_managed_company(self):
        for collection in self:
            if (
                collection.company_id
                and collection.company_id not in self.env.user.company_ids
                and not self._llm_security_bypass()
            ):
                raise ValidationError("You cannot assign an unavailable company.")

    @api.model
    def llm_discover(self, query=None, company_id=None, offset=0, limit=50):
        limit = max(1, min(int(limit or 50), 100))
        offset = max(0, int(offset or 0))
        domain = [("active", "=", True)]
        if query:
            domain = expression.AND(
                [
                    domain,
                    ["|", ("name", "ilike", query), ("description", "ilike", query)],
                ]
            )
        if company_id is not None:
            companies = self._llm_validate_company_context()
            if company_id and company_id not in companies.ids:
                raise AccessError("The requested company is not permitted.")
            domain.append(("company_id", "=", company_id or False))
        collections = self.search(
            domain,
            offset=offset,
            limit=limit,
            order="name, id",
        )
        visible = collections.filtered(
            lambda item: not item.resource_ids
            or bool(item.resource_ids._llm_authorized_records())
        )
        return [
            {
                "id": item.id,
                "name": item.name,
                "description": item.description or "",
                "company_id": item.company_id.id or None,
                "company_name": item.company_id.name or None,
            }
            for item in visible
        ]

    @api.model
    def llm_get_or_create_managed(self, key, name, company=None, source=None):
        collection = self.sudo().search([("managed_key", "=", key)], limit=1)
        values = {"name": name, "company_id": company.id if company else False}
        if source:
            values.update(
                {
                    "source_model_id": self.env["ir.model"]._get_id(source._name),
                    "source_res_id": source.id,
                }
            )
        if collection:
            collection.write(values)
            return collection
        parameters = self.env["ir.config_parameter"].sudo()
        model_id = int(
            parameters.get_param(
                "llm_knowledge_security.default_embedding_model_id", "0"
            )
        )
        store_id = int(
            parameters.get_param("llm_knowledge_security.default_store_id", "0")
        )
        if not model_id or not store_id:
            raise UserError(
                "Configure the default knowledge embedding model and vector store."
            )
        values.update(
            {
                "managed_key": key,
                "embedding_model_id": model_id,
                "store_id": store_id,
            }
        )
        return self.sudo().create(values)