# llm_mcp 17.0 → 19.0 port notes

Scope: this module only. Copied from MokbelandCo `odoo-llm` branch `17.0`
(`llm_mcp`), which itself was a 16.0 client adapted onto the 17.0 tool stack.

## Upstream

- **Source:** MokbelandCo `odoo-llm` branch `17.0`, module `llm_mcp`
  (commit that merged `cursor/odoo-llm-mcp-client-2b90`).
- **Missing on 18.0 / original 19.0:** removed in `91fca4a4`
  ("Remove non-ready modules from 18.0-migration"). 18.0 kept
  `llm_mcp_server` only; the 19.0 port of the suite skipped the client.

## 19.0 adaptations (on top of the 17.0 client)

| Area | Change |
|------|--------|
| Manifest | `19.0.1.0.0` |
| Views | `<tree>` → `<list>`; `view_mode` `list,form`; `<chatter />`; search `group name="group_by"` (no `expand`) |
| Access | Manager ACL uses `llm.group_llm_manager` (Odoo 19 privilege groups) |
| `llm.tool.execute` | Keep MCP override: 19.0 still pydantic-validates `{implementation}_execute` signatures. `mcp_execute(**parameters)` cannot describe a remote JSON schema |
| `get_input_schema` | Keep stored MCP `inputSchema`; do not generate from `mcp_execute` |
| Protocol | Client `protocolVersion` `2025-03-26` (unchanged from 17.0) |
| Process lifecycle | `MCPBusManager.discard()` on stop/unlink; skip registry restart when `test_enable` is set |

## Carried from 17.0 (not in 16.0)

- Menu label **MCP Clients** so it coexists with `llm_mcp_server`
- View modifiers already use Odoo 17+ `invisible=` / `required=`
- Bus bridge subscribe uses the server's `db_name` (no `odoo.service.db.db_list`)
- `PYTHONUNBUFFERED=1` for stdio children
