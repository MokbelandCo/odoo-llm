# odoo-llm 17.0 ↔ 19.0 porting

Scope: this fork (`MokbelandCo/odoo-llm`). Shared dual-branch rules live in the parent workspace at `.cursor/rules/odoo-17-19-branch-sync.mdc`.

## Identity source

Product names (models, stored fields, public methods, XML IDs, HTTP paths, selection keys, SQL constraint names) follow **19.0**. Version shims stay on the branch that needs them.

This document records the 19.0 → 17.0 port of MCP unique URLs, tool allowlist, authentication policy, OAuth secret wizard, assistant/tool access, and the global AI popup.

## 17.0 shims (do not copy onto 19.0)

| Area | 17.0 | 19.0 |
|------|------|------|
| List views | `<tree>`, `view_mode` `tree,form` | `<list>`, `list,form` |
| Chatter | `<div class="oe_chatter">…</div>` | `<chatter/>` |
| SQL constraints | `_sql_constraints` with the same tuple name | `models.Constraint` |
| Mail serialization | `message_format` / `_get_message_format_fields` | `_to_store` / Store |
| JSON-RPC routes | `type="json"` | `type="jsonrpc"` |
| Groups | `user.groups_id`, `res.groups.category_id` | `user.all_group_ids`, `privilege_id` / `res.groups.privilege` |
| API keys | `res.users.apikeys._generate(scope, name)` | extra `expiration_date` |
| HTTP test helper | `JsonHttpCase` wrapping `url_open(..., json=)` | native `url_open(json=)` |
| HTTP bearer API keys | 17-only `ir.http._auth_method_bearer` shim (Odoo 17 core has no `_auth_method_bearer`; MCP still calls that name) | Core `_auth_method_bearer`; do not copy the 17 shim |
| Access checks in tests | `check_access_rule` | `has_access` / `check_access` |
| Popup thread refresh | `loadUserThreads()` | `mailStore.fetchStoreData("init_messaging")` |
| Chat model picker | keep 17 chat-capable filter and `model.default` | 19 `is_default` display in header |
| Python deps | `mcp<2` | `mcp` |
| Manifest / migrations | `17.0.x.y.z` | `19.0.x.y.z` |
| Frontend tests | QUnit / skip HOOT | HOOT `llm_popup_hub.test.js` |

## Skipped on 17.0

- `llm_thread/static/tests/**` HOOT suite and `tests/test_popup_hub_js.py` (Odoo 19 test runner).
- `llm.privilege_llm` / `res.groups.privilege` (Odoo 19 group API).
- Removing 17-only `_ensure_arguments_sync` on prompt create/write.
- Replacing `_check_recursion()` with `_has_cycle()`.

`llm_mcp` (HTTP client + OAuth callback) was already on 17.0 and was not re-derived from 19.0 in this port.

`llm_dummy` is a local/dev helper: service name `dummy`, XML IDs `llm_provider_dummy` / `llm_model_dummy_chat`. Port the same names to 19.0 when that branch needs popup/chat UI without an external API.

## Shared names introduced by this port

Keep these identical when porting the other direction:

- Models: `llm.mcp.oauth.client.secret.show`
- Fields: `endpoint_path`, `authentication_policy`, `tool_mode`, `tool_ids`, `category` (on `llm.tool`), `allowed_group_ids`, `is_public`
- Methods: `get_config_for_request`, `get_exposed_tools`, `is_tool_exposed`, `get_allowed_assistants`, `_get_allowed_assistants_for_user`, `_auth_method_mcp_bearer` (calls core `_auth_method_bearer`; 17 shims that core name), `ensureThreadLoaded`, `createNewThread({ select })`
- JS services: `llm.popup_hub`, `llm.chat_hub`
- Dummy provider: service `dummy`, XML IDs `llm_provider_dummy`, `llm_model_dummy_chat`
- Constraints: `endpoint_path_unique`, `client_id_unique`, `session_id_unique`

## Workflow

Port behavior, not a raw `git merge` of 19.0 into 17.0. After a change lands on either branch, inventory names against the other branch before tests.
