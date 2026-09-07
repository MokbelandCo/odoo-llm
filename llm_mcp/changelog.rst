19.0.1.0.0 (2026-09-07)
~~~~~~~~~~~~~~~~~~~~~~~

* [ADD] Ported MCP client from MokbelandCo 17.0 ``llm_mcp`` onto the 19.0 tool stack
* [IMP] Odoo 19 ``<list>`` views, ``view_mode``, and ``<chatter />``
* [IMP] Manager access via ``llm.group_llm_manager``
* [IMP] Skip pydantic-from-signature validation for remote MCP tools
* [IMP] Negotiate MCP protocol ``2025-03-26`` instead of legacy ``0.1.0``
* [IMP] Stop stdio processes on unlink / stop, and skip registry restart during tests
* [ADD] Integration tests against a local mock MCP stdio server
