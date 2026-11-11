"""Record link tokens for built-in Odoo chats.

Token syntax (copied by the model from a tool result ``link_token``)::

    [[odoo-ref:<ref>]]
    [[odoo-ref:<ref>|<link text>]]

``<ref>`` is a 12-character alphanumeric identifier issued by the server when
a tool result contains an ``llm_tool.record_reference`` dict. The issued
reference is stored on the tool message (``body_json["record_refs"]``) with
an HMAC bound to the thread, so a reference only resolves inside the thread
that issued it. See ``llm_thread/docs/RECORD_LINKS.md``.
"""

import re
import secrets
import string

from lxml import etree
from lxml import html as lxml_html

from odoo.exceptions import AccessError, MissingError
from odoo.tools import misc

from odoo.addons.llm_tool.record_reference import (
    clean_label,
    iter_record_references,
    record_backend_path,
)

# Set by the chat UI streaming endpoint; enables the shared system instruction.
CONTEXT_KEY = "llm_record_links"
TOKEN_MARKER = "[[odoo-ref:"
TOKEN_RE = re.compile(r"\[\[odoo-ref:([^\[\]|\n]{0,64})(?:\|([^\[\]\n]{0,200}))?\]\]")
REF_ALPHABET = string.ascii_letters + string.digits
REF_LENGTH = 12
REF_RE = re.compile(rf"\A[A-Za-z0-9]{{{REF_LENGTH}}}\Z")
LINK_TEXT_MAX_LENGTH = 120
SIGNATURE_SCOPE = "llm_thread.record_ref"
LINK_CLASS = "o_llm_record_link"
UNAVAILABLE_CLASS = "o_llm_record_link_unavailable"
# Tokens inside these elements stay literal text.
SKIP_TAGS = frozenset(
    {"a", "code", "pre", "kbd", "samp", "script", "style", "textarea", "button"}
)


def new_ref_id():
    return "".join(secrets.choice(REF_ALPHABET) for _ in range(REF_LENGTH))


def make_token(ref_id):
    return f"[[odoo-ref:{ref_id}]]"


def sign_ref(env, thread_id, entry):
    message = (
        int(thread_id),
        entry["ref"],
        entry["model"],
        int(entry["res_id"]),
        entry["label"],
    )
    return misc.hmac(env(su=True), SIGNATURE_SCOPE, message)


def is_valid_entry(env, thread_id, entry):
    """Check shape and HMAC of a stored ``record_refs`` entry."""
    if not isinstance(entry, dict):
        return False
    ref_id = entry.get("ref")
    res_id = entry.get("res_id")
    if not (
        isinstance(ref_id, str)
        and REF_RE.match(ref_id)
        and isinstance(entry.get("model"), str)
        and isinstance(res_id, int)
        and not isinstance(res_id, bool)
        and isinstance(entry.get("label"), str)
        and isinstance(entry.get("sig"), str)
    ):
        return False
    return secrets.compare_digest(entry["sig"], sign_ref(env, thread_id, entry))


def get_readable_record(env, model_name, res_id):
    """Return the record when the current user may read it, else ``None``.

    Uses the standard access rights and record rules (including multi-company
    rules) of ``env``. Abstract and transient models are never linkable.
    """
    if model_name not in env:
        return None
    model = env[model_name]
    if model._abstract or model._transient:
        return None
    if not model.check_access_rights("read", raise_exception=False):
        return None
    record = model.browse(res_id)
    try:
        if not record.exists():
            return None
        record.check_access_rule("read")
    except (AccessError, MissingError):
        return None
    return record


def issue_record_refs(env, thread_id, result):
    """Register record references found in a tool ``result``.

    Mutates ``result`` in place: each readable reference gets a
    ``link_token``; unreadable ones get ``link_token: None``. Duplicate
    references to the same record share one issued reference.

    Returns:
        list: entries to store in ``body_json["record_refs"]``
    """
    entries = []
    by_key = {}
    for reference in list(iter_record_references(result)):
        key = (reference["model"], reference["id"])
        if key not in by_key:
            record = get_readable_record(env, *key)
            entry = None
            if record:
                entry = {
                    "ref": new_ref_id(),
                    "model": key[0],
                    "res_id": key[1],
                    "label": clean_label(reference.get("label"))
                    or clean_label(record.display_name),
                }
                entry["sig"] = sign_ref(env, thread_id, entry)
                entries.append(entry)
            by_key[key] = entry
        entry = by_key[key]
        reference["link_token"] = make_token(entry["ref"]) if entry else None
    return entries


def link_payload(entry):
    """Client payload for a resolved reference (fallback links)."""
    return {
        "ref": entry["ref"],
        "model": entry["model"],
        "id": entry["res_id"],
        "label": entry["label"],
        "url": record_backend_path(entry["model"], entry["res_id"]),
    }


def _clean_link_text(text):
    text = " ".join((text or "").split())
    return text[:LINK_TEXT_MAX_LENGTH]


def _build_anchor(entry, text, title):
    anchor = etree.Element("a")
    anchor.set("href", record_backend_path(entry["model"], entry["res_id"]))
    anchor.set("class", LINK_CLASS)
    anchor.set("data-oe-model", entry["model"])
    anchor.set("data-oe-id", str(entry["res_id"]))
    anchor.set("title", title)
    anchor.text = text or entry["label"]
    return anchor


def _build_unavailable(text, unavailable_text):
    span = etree.Element("span")
    span.set("class", f"{UNAVAILABLE_CLASS} text-muted")
    span.set("title", unavailable_text)
    span.text = text or unavailable_text
    return span


def render_record_tokens(body, resolve, unavailable_text, title_format):
    """Replace complete tokens in a stored HTML ``body`` with record anchors.

    Args:
        body: stored (already sanitized) message HTML
        resolve: callable ``ref_id -> entry dict | None``
        unavailable_text: text for references that do not resolve
        title_format: ``"Open %s"``-style format for the anchor title

    Returns:
        str: rendered HTML, or ``body`` unchanged when it has no token
    """
    if not body or TOKEN_MARKER not in body:
        return body

    def build(match):
        ref_id, text = match.group(1), _clean_link_text(match.group(2))
        entry = resolve(ref_id) if REF_RE.match(ref_id or "") else None
        if not entry:
            return _build_unavailable(text, unavailable_text)
        return _build_anchor(entry, text, title_format % entry["label"])

    def split(value):
        """Return (leading_text, [(node, tail_text), ...]) for ``value``."""
        parts = []
        position = 0
        leading = None
        for match in TOKEN_RE.finditer(value):
            chunk = value[position : match.start()]
            if parts:
                parts[-1] = (parts[-1][0], chunk)
            else:
                leading = chunk
            parts.append((build(match), ""))
            position = match.end()
        if not parts:
            return value, []
        parts[-1] = (parts[-1][0], value[position:])
        return leading, parts

    def walk(element):
        children = list(element)
        if element.tag not in SKIP_TAGS:
            if element.text and TOKEN_MARKER in element.text:
                leading, parts = split(element.text)
                element.text = leading
                for index, (node, tail) in enumerate(parts):
                    node.tail = tail
                    element.insert(index, node)
            for child in children:
                if isinstance(child.tag, str):
                    walk(child)
                if child.tail and TOKEN_MARKER in child.tail:
                    leading, parts = split(child.tail)
                    child.tail = leading
                    position = element.index(child)
                    for offset, (node, tail) in enumerate(parts, start=1):
                        node.tail = tail
                        element.insert(position + offset, node)

    root = lxml_html.fragment_fromstring(str(body), create_parent="div")
    walk(root)
    rendered = etree.tostring(root, encoding="unicode", method="html")
    return rendered[len("<div>") : -len("</div>")]
