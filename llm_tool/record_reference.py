"""Structured record references for tool results.

A tool that wants the chat to offer a link to an Odoo record returns a
record reference instead of HTML or a hand-built URL::

    from odoo.addons.llm_tool.record_reference import record_reference

    @llm_tool(read_only_hint=True)
    def find_thing(self, name: str) -> dict:
        thing = self.env["my.thing"].search([("name", "ilike", name)], limit=1)
        return {"found": bool(thing), "thing": record_reference(thing)}

The returned dict is plain JSON. External clients (MCP) can use ``url`` and
``label`` directly. Built-in Odoo chats (``llm_thread``) additionally register
the reference on the tool message and add a ``link_token`` that the model
copies into its reply; see ``llm_thread/docs/RECORD_LINKS.md``.
"""

from urllib.parse import urlencode, urlsplit

RECORD_REFERENCE_TYPE = "odoo_record_reference"
LABEL_MAX_LENGTH = 200


def record_backend_path(model_name, res_id):
    """Return the relative backend form URL for ``model_name``/``res_id``."""
    query = urlencode({"model": model_name, "id": int(res_id), "view_type": "form"})
    return f"/web#{query}"


def record_backend_url(record):
    """Return an absolute http(s) backend form URL for ``record``.

    Falls back to the relative path when ``web.base.url`` is not an http(s)
    URL, so the result never carries an executable scheme.
    """
    record.ensure_one()
    path = record_backend_path(record._name, record.id)
    base_url = (record.get_base_url() or "").rstrip("/")
    if urlsplit(base_url).scheme in ("http", "https"):
        return f"{base_url}{path}"
    return path


def clean_label(label):
    """Normalize a label to a single line of bounded length."""
    text = " ".join(str(label or "").split())
    if len(text) > LABEL_MAX_LENGTH:
        text = text[: LABEL_MAX_LENGTH - 1] + "\u2026"
    return text


def record_reference(record, label=None):
    """Build a structured reference to ``record`` for a tool result.

    :param record: singleton recordset the current user can read
    :param label: optional user-facing label; defaults to ``display_name``.
        Pass a reduced label when the full display name must not appear in
        the conversation (privacy).
    :return: ``{"type", "model", "id", "label", "url"}`` or ``None`` for an
        empty recordset
    """
    if not record:
        return None
    record.ensure_one()
    return {
        "type": RECORD_REFERENCE_TYPE,
        "model": record._name,
        "id": record.id,
        "label": clean_label(label if label is not None else record.display_name),
        "url": record_backend_url(record),
    }


def is_record_reference(value):
    """Return True when ``value`` looks like a :func:`record_reference` dict."""
    return (
        isinstance(value, dict)
        and value.get("type") == RECORD_REFERENCE_TYPE
        and isinstance(value.get("model"), str)
        and isinstance(value.get("id"), int)
        and not isinstance(value.get("id"), bool)
    )


def iter_record_references(value, _depth=0):
    """Yield every record reference dict nested in a JSON-like ``value``."""
    if _depth > 20:
        return
    if is_record_reference(value):
        yield value
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from iter_record_references(item, _depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from iter_record_references(item, _depth + 1)
