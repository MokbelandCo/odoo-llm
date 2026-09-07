# llm_mcp 16.0 → 17.0 port notes

Scope: this module only. Shared odoo-llm 17.0 port notes live in the repository README.

## Upstream

- **Source:** Apexive / MokbelandCo `odoo-llm` branch `16.0`, module `llm_mcp`.
- **Missing on 18.0 / 19.0:** removed in `91fca4a4` ("Remove non-ready modules from 18.0-migration"). 18.0 kept `llm_mcp_server` only.
- **17.0 branch origin:** true backport of current 18.0, so it never received the client.

## 17.0 adaptations (not in 16.0)

| Area | Change |
|------|--------|
| Manifest | `17.0.1.0.0`; menu label **MCP Clients** so it coexists with `llm_mcp_server` |
| Views | `attrs` → Odoo 17 `invisible` / `required`; keep `<tree>` and `oe_chatter` |
| `llm.tool.execute` | Override MCP path: 17.0 pydantic-from-signature cannot describe remote JSON schemas |
| `get_input_schema` | Use stored MCP `inputSchema`; do not generate from `mcp_execute(**parameters)` |
| Protocol | Client `protocolVersion` `2025-03-26` (16.0 used `0.1.0`) |
| Process lifecycle | `MCPBusManager.discard()` on stop/unlink; `PYTHONUNBUFFERED=1` for stdio children |
| Bus bridge | Subscribe with the server's `db_name` (Odoo 17 has no `odoo.service.db.db_list`) |
| Registry hook | Skip auto-restart of stdio servers when `test_enable` is set |
