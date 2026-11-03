from __future__ import annotations

from odoo import models
from odoo.exceptions import AccessError

from odoo.addons.llm_tool.decorators import llm_tool


class LLMKnowledgeSecurityTools(models.TransientModel):
    _name = "llm.knowledge.security.tools"
    _description = "Secured Knowledge Discovery Tools"

    def _collections(self, query=None, company_id=None, offset=0, limit=50):
        collection_model = self.env["llm.knowledge.collection"]
        if company_id is not None:
            permitted = collection_model._llm_validate_company_context()
            if company_id and company_id not in permitted.ids:
                raise AccessError("The requested company is not permitted.")
            collection_model = collection_model.with_context(
                allowed_company_ids=[company_id] if company_id else permitted.ids
            )
        return collection_model.llm_discover(
            query=query,
            company_id=company_id,
            offset=offset,
            limit=limit,
        )

    @llm_tool(read_only_hint=True, idempotent_hint=True)
    def knowledge_collection_list(
        self,
        company_id: int | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict:
        """List authorized active knowledge collections in deterministic name order."""
        values = self._collections(
            company_id=company_id,
            offset=offset,
            limit=limit,
        )
        return {"count": len(values), "collections": values}

    @llm_tool(read_only_hint=True, idempotent_hint=True)
    def knowledge_collection_search(
        self,
        query: str,
        company_id: int | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict:
        """Search authorized collections by name or description."""
        values = self._collections(
            query=query,
            company_id=company_id,
            offset=offset,
            limit=limit,
        )
        return {"query": query, "count": len(values), "collections": values}