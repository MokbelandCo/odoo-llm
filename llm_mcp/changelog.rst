17.0.1.0.0 (2026-09-06)
~~~~~~~~~~~~~~~~~~~~~~~

* [ADD] Ported MCP client from Apexive 16.0 ``llm_mcp`` onto the 17.0 tool stack
* [IMP] Odoo 17 view modifiers, chatter, and ``<tree>`` syntax
* [IMP] Negotiate MCP protocol ``2025-03-26`` instead of legacy ``0.1.0``
* [IMP] Skip pydantic-from-signature validation for remote MCP tools
* [IMP] Stop stdio processes on unlink / stop, and skip registry restart during tests
* [ADD] Integration tests against a local mock MCP stdio server
