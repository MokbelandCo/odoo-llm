"""Responses API translation for the Odoo LLM chat contract.

Conversation state strategy: manual, stateless replay. Every request sends
``store: false`` and rebuilds ``input`` from the Odoo thread (the source of
truth for edits, deletions and retention). Reasoning items are requested with
``include: ["reasoning.encrypted_content"]`` and persisted on the assistant
``mail.message`` (``llm_provider_state``), then replayed in their original
order before the function calls / text they produced. ``previous_response_id``
is never used, so nothing depends on OpenAI keeping conversation state and no
history can cross from one thread to another.

The provider-neutral chunk contract consumed by ``llm.thread`` is:
``{"content": str}``, ``{"tool_calls": [...]}``, ``{"provider_state": {...}}``
and optionally ``usage`` / ``finish_reason``. Errors raise
:class:`OpenAIChatError`.
"""

import json
import re

from odoo.exceptions import UserError

from .openai_capabilities import ENDPOINT_PATHS, RESPONSES

STATE_KEY = "openai"
#: Private key carried on formatted assistant dicts between format_message
#: and the Responses translation; stripped before any Chat Completions call.
REPLAY_KEY = "_openai_state"

_SECRET_RE = re.compile(r"(sk-|sess-|ek_)[A-Za-z0-9_\-\*]{4,}")


def redact(text, limit=300):
    """Remove credential-shaped tokens and cap the length of a provider message."""
    text = _SECRET_RE.sub(r"\1***", str(text or ""))
    return text[:limit]


class OpenAIChatError(UserError):
    """A chat request OpenAI cannot serve, with an actionable explanation.

    ``details`` holds only model, endpoint, parameter and provider error
    code/type; never credentials, prompts or response bodies.
    """

    def __init__(self, message, model=None, endpoint=None, param=None, code=None,
                 status=None, retryable=False):
        super().__init__(message)
        self.details = {
            "model": model,
            "endpoint": ENDPOINT_PATHS.get(endpoint, endpoint),
            "param": param,
            "code": code,
            "status": status,
            "retryable": retryable,
        }

    def log_details(self):
        return {key: value for key, value in self.details.items() if value is not None}


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _dump(obj):
    if obj is None or isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump(exclude_none=True)
    return dict(vars(obj))


# ----------------------------------------------------------------------
# Request translation
# ----------------------------------------------------------------------
def _text_part(text, role):
    return {"type": "output_text" if role == "assistant" else "input_text", "text": text}


def _content_parts(content, role, model, endpoint=RESPONSES):
    if content is None:
        return []
    if isinstance(content, str):
        return [_text_part(content, role)] if content else []
    parts = []
    for item in content:
        if isinstance(item, str):
            parts.append(_text_part(item, role))
            continue
        kind = item.get("type")
        if kind in ("text", "input_text", "output_text"):
            if item.get("text"):
                parts.append(_text_part(item["text"], role))
        elif kind == "image_url":
            image = item.get("image_url") or {}
            url = image.get("url") if isinstance(image, dict) else image
            part = {"type": "input_image", "image_url": url}
            detail = image.get("detail") if isinstance(image, dict) else None
            part["detail"] = detail or "auto"
            parts.append(part)
        elif kind == "input_image":
            parts.append(item)
        elif kind == "file":
            file_data = item.get("file") or {}
            part = {"type": "input_file"}
            for key in ("filename", "file_data", "file_id"):
                if file_data.get(key):
                    part[key] = file_data[key]
            parts.append(part)
        elif kind == "input_file":
            parts.append(item)
        elif kind == "input_audio":
            raise OpenAIChatError(
                "Audio attachments cannot be sent to model %s through %s. "
                "Remove the audio attachment or use a speech-to-text model first."
                % (model, ENDPOINT_PATHS[endpoint]),
                model=model,
                endpoint=endpoint,
                param="input.content.input_audio",
                code="unsupported_attachment",
            )
        else:
            raise OpenAIChatError(
                "Message content of type '%s' is not supported by %s for model %s."
                % (kind, ENDPOINT_PATHS[endpoint], model),
                model=model,
                endpoint=endpoint,
                param="input.content",
                code="unsupported_content",
            )
    return parts


def _function_call_item(tool_call):
    function = tool_call.get("function") or {}
    return {
        "type": "function_call",
        "call_id": tool_call["id"],
        "name": function.get("name"),
        "arguments": function.get("arguments") or "{}",
    }


def _assistant_items(message, model):
    """Assistant turn → output items, replaying stored reasoning in order."""
    tool_calls = message.get("tool_calls") or []
    calls_by_id = {tc["id"]: tc for tc in tool_calls if tc.get("id")}
    state = message.get(REPLAY_KEY) or {}
    text = state.get("text") if state.get("text_matches_body") else None
    if text is None:
        text = message.get("content")
        if isinstance(text, list):
            text = "\n".join(
                part.get("text", "") for part in text if isinstance(part, dict)
            )
    same_model = state.get("model") == model and state.get("text_matches_body", True)

    items = []
    used_calls = set()
    text_done = False
    for entry in state.get("items") or []:
        kind = entry.get("type")
        if kind == "reasoning":
            if same_model and entry.get("encrypted_content"):
                reasoning = {
                    "type": "reasoning",
                    "id": entry.get("id"),
                    "summary": entry.get("summary") or [],
                    "encrypted_content": entry["encrypted_content"],
                }
                items.append({k: v for k, v in reasoning.items() if v is not None})
        elif kind == "message":
            if text and not text_done:
                items.append(_assistant_message(text))
                text_done = True
        elif kind == "function_call":
            call = calls_by_id.get(entry.get("call_id"))
            if call and call["id"] not in used_calls:
                items.append(_function_call_item(call))
                used_calls.add(call["id"])
    if text and not text_done:
        # No stored order (Chat Completions turn or older message): the text
        # goes after any reasoning and before the function calls.
        position = next(
            (i for i, item in enumerate(items) if item["type"] == "function_call"),
            len(items),
        )
        items.insert(position, _assistant_message(text))
    for call in tool_calls:
        if call.get("id") and call["id"] not in used_calls:
            items.append(_function_call_item(call))
            used_calls.add(call["id"])
    return items


def _assistant_message(text):
    return {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
    }


def messages_to_input(messages, model):
    """Translate Chat-Completions-shaped Odoo messages to Responses input items.

    System guidance becomes ``developer`` messages at their original position
    (prefix order is preserved for caching and reasoning continuity), tool
    results become ``function_call_output`` items keyed by ``call_id``.
    """
    items = []
    for message in messages:
        role = message.get("role")
        if role in ("system", "developer"):
            parts = _content_parts(message.get("content"), "developer", model)
            if parts:
                items.append({"type": "message", "role": "developer", "content": parts})
        elif role == "user":
            parts = _content_parts(message.get("content"), "user", model)
            if parts:
                items.append({"type": "message", "role": "user", "content": parts})
        elif role == "assistant":
            items.extend(_assistant_items(message, model))
        elif role == "tool":
            output = message.get("content")
            if not isinstance(output, str):
                output = json.dumps(output)
            items.append({
                "type": "function_call_output",
                "call_id": message.get("tool_call_id"),
                "output": output or "",
            })
    _check_call_pairing(items, model)
    return items


def _check_call_pairing(items, model):
    """Every function_call_output must follow its function_call."""
    seen = set()
    for item in items:
        if item.get("type") == "function_call":
            seen.add(item["call_id"])
        elif item.get("type") == "function_call_output" and item["call_id"] not in seen:
            raise OpenAIChatError(
                "Tool result %s has no matching tool call in the conversation sent to %s."
                % (item["call_id"], model),
                model=model,
                endpoint=RESPONSES,
                param="input.function_call_output.call_id",
                code="orphan_tool_result",
            )


def strip_replay_state(messages):
    """Chat Completions payloads must not carry the private replay key."""
    cleaned = []
    for message in messages:
        if REPLAY_KEY in message:
            message = {k: v for k, v in message.items() if k != REPLAY_KEY}
        cleaned.append(message)
    return cleaned


def chat_tool_to_responses(tool):
    """Chat Completions tool dict → Responses function tool.

    ``strict`` is sent explicitly as ``false``: Responses defaults function
    tools to strict mode, which rejects the optional properties Odoo tool
    schemas use.
    """
    function = tool.get("function") or {}
    return {
        "type": "function",
        "name": function.get("name"),
        "description": function.get("description") or "",
        "parameters": function.get("parameters") or {"type": "object", "properties": {}},
        "strict": False,
    }


def chat_tool_choice_to_responses(choice):
    if isinstance(choice, dict):
        name = (choice.get("function") or {}).get("name") or choice.get("name")
        return {"type": "function", "name": name}
    return choice


# ----------------------------------------------------------------------
# Response normalization
# ----------------------------------------------------------------------
def _usage(response):
    usage = _dump(_get(response, "usage"))
    if not usage:
        return None
    details = usage.get("output_tokens_details") or {}
    return {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "reasoning_tokens": details.get("reasoning_tokens"),
        "total_tokens": usage.get("total_tokens"),
    }


def _error_from_response(response, model, fallback):
    error = _dump(_get(response, "error")) or {}
    code = error.get("code") or fallback
    message = redact(error.get("message") or "")
    text = "OpenAI could not complete the response for model %s on %s (%s)." % (
        model, ENDPOINT_PATHS[RESPONSES], code,
    )
    if message:
        text += " " + message
    return OpenAIChatError(text, model=model, endpoint=RESPONSES, code=code)


def _incomplete_error(response, model, has_tool_calls):
    details = _dump(_get(response, "incomplete_details")) or {}
    reason = details.get("reason") or "unknown"
    if has_tool_calls:
        hint = "The tool calls were not executed because the response was cut off."
    else:
        hint = "No answer was produced."
    if reason == "max_output_tokens":
        hint += " Increase the maximum output tokens or lower the reasoning effort."
    return OpenAIChatError(
        "OpenAI stopped the response for model %s early (%s). %s" % (model, reason, hint),
        model=model,
        endpoint=RESPONSES,
        param="max_output_tokens" if reason == "max_output_tokens" else None,
        code="incomplete:%s" % reason,
    )


def collect_output(output_items):
    """Split output items into text, refusal, tool calls and the replay skeleton."""
    texts = []
    refusals = []
    tool_calls = []
    skeleton = []
    seen_calls = set()
    for raw in output_items or []:
        item = _dump(raw)
        kind = item.get("type")
        if kind == "message":
            for part in item.get("content") or []:
                if part.get("type") == "output_text" and part.get("text"):
                    texts.append(part["text"])
                elif part.get("type") == "refusal" and part.get("refusal"):
                    refusals.append(part["refusal"])
            skeleton.append({"type": "message"})
        elif kind == "function_call":
            call_id = item.get("call_id")
            if not call_id or call_id in seen_calls:
                continue
            seen_calls.add(call_id)
            tool_calls.append({
                "id": call_id,
                "type": "function",
                "function": {
                    "name": item.get("name"),
                    "arguments": item.get("arguments") or "{}",
                },
            })
            skeleton.append({"type": "function_call", "call_id": call_id})
        elif kind == "reasoning":
            entry = {"type": "reasoning", "id": item.get("id")}
            if item.get("encrypted_content"):
                entry["encrypted_content"] = item["encrypted_content"]
            if item.get("summary"):
                entry["summary"] = item["summary"]
            skeleton.append(entry)
    return texts, refusals, tool_calls, skeleton


def _provider_state(model, text, skeleton):
    """Replay state; ``text`` is exactly what the thread was given as content."""
    return {
        STATE_KEY: {
            "model": model,
            "endpoint": RESPONSES,
            "text": text,
            "items": skeleton,
        }
    }


def normalize_response(response, model):
    """Non-streaming Responses object → one standardized dict."""
    status = _get(response, "status") or "completed"
    texts, refusals, tool_calls, skeleton = collect_output(_get(response, "output"))
    if status == "failed":
        raise _error_from_response(response, model, "failed")
    if status == "incomplete" and (tool_calls or not (texts or refusals)):
        raise _incomplete_error(response, model, bool(tool_calls))
    if status not in ("completed", "incomplete"):
        raise OpenAIChatError(
            "OpenAI returned response status '%s' for model %s." % (status, model),
            model=model, endpoint=RESPONSES, code="status:%s" % status,
        )
    result = {}
    content = "".join(texts)
    if refusals:
        content = (content + "\n" if content else "") + "\n".join(refusals)
    if content:
        result["content"] = content
    if tool_calls:
        result["tool_calls"] = tool_calls
    result["provider_state"] = _provider_state(model, content, skeleton)
    usage = _usage(response)
    if usage:
        result["usage"] = usage
    result["finish_reason"] = _finish_reason(status, tool_calls, refusals, response)
    return result


def _finish_reason(status, tool_calls, refusals, response):
    if tool_calls:
        return "tool_calls"
    if refusals:
        return "refusal"
    if status == "incomplete":
        details = _dump(_get(response, "incomplete_details")) or {}
        return "length" if details.get("reason") == "max_output_tokens" else "incomplete"
    return "stop"


def response_chunks(result):
    """Turn a normalized non-streaming result into stream chunks."""
    if result.get("content"):
        yield {"content": result["content"]}
    if result.get("tool_calls"):
        yield {"tool_calls": result["tool_calls"]}
    tail = {k: result[k] for k in ("provider_state", "usage", "finish_reason") if k in result}
    if tail:
        yield tail


def stream_chunks(events, model):
    """Typed Responses stream events → standardized chunks.

    Text is streamed as it arrives. Tool calls are only released once
    ``response.completed`` arrives, so an interrupted or failed stream can
    never trigger a partial or duplicate tool execution.
    """
    emitted = []
    terminal = None
    for event in events:
        kind = _get(event, "type")
        if kind in ("response.output_text.delta", "response.refusal.delta"):
            delta = _get(event, "delta")
            if delta:
                emitted.append(delta)
                yield {"content": delta}
        elif kind == "response.completed":
            terminal = _get(event, "response")
            break
        elif kind == "response.incomplete":
            response = _get(event, "response")
            texts, refusals, tool_calls, _skeleton = collect_output(_get(response, "output"))
            if tool_calls or not emitted:
                raise _incomplete_error(response, model, bool(tool_calls))
            terminal = response
            break
        elif kind == "response.failed":
            raise _error_from_response(_get(event, "response"), model, "failed")
        elif kind == "error":
            code = _get(event, "code") or "stream_error"
            raise OpenAIChatError(
                "OpenAI reported a streaming error for model %s on %s (%s). %s"
                % (model, ENDPOINT_PATHS[RESPONSES], code, redact(_get(event, "message"))),
                model=model, endpoint=RESPONSES, code=code,
                param=_get(event, "param"),
            )
    if terminal is None:
        raise OpenAIChatError(
            "The OpenAI stream for model %s ended before the response was completed. "
            "No tool was executed; send the message again." % model,
            model=model, endpoint=RESPONSES, code="stream_interrupted", retryable=True,
        )
    status = _get(terminal, "status") or "completed"
    texts, refusals, tool_calls, skeleton = collect_output(_get(terminal, "output"))
    final_text = "".join(texts) or "\n".join(refusals)
    streamed = "".join(emitted)
    if final_text.startswith(streamed) and len(final_text) > len(streamed):
        remainder = final_text[len(streamed):]
        emitted.append(remainder)
        yield {"content": remainder}
    if tool_calls:
        yield {"tool_calls": tool_calls}
    tail = {
        "provider_state": _provider_state(model, "".join(emitted), skeleton),
        "finish_reason": _finish_reason(status, tool_calls, refusals, terminal),
    }
    usage = _usage(terminal)
    if usage:
        tail["usage"] = usage
    yield tail
