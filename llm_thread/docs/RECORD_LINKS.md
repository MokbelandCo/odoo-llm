# Record links in AI chats

Built-in Odoo chats (full-screen **Chat** action, global AI popup, chatter AI
chat) can show links to Odoo records that a tool found. Clicking a link opens
the record form in the current action stack: no page reload, the chat stays in
the breadcrumbs and the popup stays open.

## For tool authors

Return a record reference from the tool. Do not build HTML or URLs.

```python
from odoo.addons.llm_tool.decorators import llm_tool
from odoo.addons.llm_tool.record_reference import record_reference


@llm_tool(read_only_hint=True)
def find_contact(self, name: str) -> dict:
    partner = self.env["res.partner"].search([("name", "ilike", name)], limit=1)
    return {"found": bool(partner), "contact": record_reference(partner)}
```

`record_reference(record, label=None)` returns plain JSON:

| key | value |
| --- | --- |
| `type` | `"odoo_record_reference"` |
| `model`, `id` | the record |
| `label` | `display_name`, or the `label` you pass (single line, max 200 chars) |
| `url` | absolute `/web#model=..&id=..&view_type=form` URL (relative if `web.base.url` is not http(s)) |

References can be nested anywhere in the result (lists, dicts). Pass a reduced
`label` when the display name must not appear in the conversation.

External clients (MCP server) receive this JSON as-is and can use `url` and
`label`. Nothing else changes for them: no token and no extra instruction.

## What happens in a built-in chat

1. **Issue.** After the tool runs, `mail.message._postprocess_tool_data`
   (`llm_thread`) finds the references, checks that the current user can read
   each record, and issues a random 12-character reference per record. The
   result gets `link_token: "[[odoo-ref:AbCdEf123456]]"` (or `null` when the
   record is not readable). The issued entries are stored on the tool message
   in `body_json["record_refs"]`, each signed with an HMAC bound to the thread
   (`odoo.tools.misc.hmac`, scope `llm_thread.record_ref`).
2. **Instruct.** The `/llm/thread/generate` endpoint sets the context key
   `llm_record_links`. `llm.provider._prepare_prepend_messages` (the common
   system-message path, next to the tool consent text) then appends one shared
   instruction when tools are offered: copy `link_token` exactly, optionally
   `[[odoo-ref:<ref>|link text]]`, never invent tokens, no HTML/JS/URLs, no
   tokens in code. Assistants and prompts need no change. Generation started
   elsewhere (automation, MCP, jobs) does not get the instruction.
3. **Store.** The assistant reply is stored as usual (markdown to HTML) with
   the literal token. History sent back to the model keeps the token.
4. **Render.** `mail.message.message_format` resolves tokens on every read, for
   the current viewer, which covers streamed chunks, the final message,
   reopened history, full-screen and popup:
   - the ref must be issued in the same thread with a valid signature;
   - the viewer must still read the thread and the record (access rights and
     record rules, including multi-company);
   - a resolved token becomes
     `<a href="/web#..." class="o_llm_record_link" data-oe-model data-oe-id title="Open <label>">`;
     anything else (unknown, malformed, forged, other thread, deleted or no
     longer readable) becomes an inert `span.o_llm_record_link_unavailable`
     with a generic text and no label;
   - tokens inside `code`, `pre`, `a` and similar elements stay literal text;
   - an incomplete token (still streaming) is plain text until it completes.
   Tool messages also get `llm_record_links` (deduplicated, re-validated) which
   the tool result card shows as **Open <label>** links. `record_refs` itself
   is never sent to the client.
5. **Navigate.** `llm_thread/static/src/patches/store_service_patch.js`
   extends `mail.store.handleClickOnLink` for `a.o_llm_record_link`.
   `openLLMRecordLink` validates the model name and id and calls
   `action.doAction({type: "ir.actions.act_window", res_model, res_id,
   views: [[false, "form"]], target: "current"}, {clearBreadcrumbs: false})`.
   The action service runs the usual unsaved-changes check first: a dirty
   valid form is saved, an invalid one blocks navigation. Ctrl/Cmd/middle
   click keeps the native `href` (new tab). Access errors raised when the form
   loads are shown by the standard error dialog.

## Chat state across navigation

- The full-screen action no longer calls `llm.store.destroy()` on unmount, so
  running streams (popup and full-screen) continue while the chat sits in the
  breadcrumbs.
- The selected thread is exported with `useSetupAction({getLocalState})` and
  restored from `props.state` when coming back through the breadcrumbs.
- Composer drafts and scroll positions live on the `mail.store` thread and
  composer records, so they survive the remount.
- The popup is a main component with its own layout service; it is not part of
  the action stack.

## Limitations

- The raw tool result JSON (expandable section of the tool card) shows the
  labels returned when the tool ran. Links and fallback links are re-validated
  on every read; the raw JSON is not.
- If markdown formatting splits a token (for example emphasis inside the link
  text), the token is not recognized and stays plain text.
- Only tool results issue references; a token typed by the user or invented by
  the model never resolves.

## Tests

- `llm_tool/tests/test_record_reference.py`: helper contract.
- `llm_thread/tests/test_record_links.py`: issuance, rendering, security cases,
  streaming, fallback links, instruction injection.
- `llm_thread/tests/test_record_links_tour.py` (+ `static/tests/tours/record_links_tour.js`):
  full-screen and popup navigation, breadcrumbs, composer draft, invalid and
  dirty forms, no reload.
- `llm_dummy/tests/test_dummy_provider.py` (`TestDummyRecordLinks`): end to end
  with the dummy provider and its `llm_dummy_find_contact` tool.
