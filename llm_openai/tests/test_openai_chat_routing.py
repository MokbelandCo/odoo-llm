"""OpenAI chat routing, parameter filtering and Responses normalization.

The OpenAI SDK client is replaced by a recorder, so these tests check the
exact request each model gets and how responses and streams are turned into
the provider-neutral chunk contract. Live checks are in
``test_openai_live_chat.py``.
"""

import json
from types import SimpleNamespace

import httpx
from openai import BadRequestError

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.llm_openai.utils.openai_capabilities import (
    CHAT_COMPLETIONS,
    RESPONSES,
    lookup,
)
from odoo.addons.llm_openai.utils.openai_responses import (
    REPLAY_KEY,
    OpenAIChatError,
    messages_to_input,
)

FAKE_KEY = "sk-test-not-a-real-key-000000"


def _cc_response(content=None, tool_calls=None, finish_reason="stop", refusal=None):
    calls = [
        SimpleNamespace(
            id=call_id,
            type="function",
            function=SimpleNamespace(name=name, arguments=arguments),
        )
        for call_id, name, arguments in (tool_calls or [])
    ]
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(content=content, tool_calls=calls, refusal=refusal),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=5, completion_tokens=3, total_tokens=8,
            completion_tokens_details=None,
        ),
    )


def _cc_chunk(content=None, tool_calls=None, finish_reason=None):
    deltas = [
        SimpleNamespace(
            index=index,
            id=call_id,
            function=SimpleNamespace(name=name, arguments=arguments),
        )
        for index, call_id, name, arguments in (tool_calls or [])
    ]
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                delta=SimpleNamespace(content=content, tool_calls=deltas or None, refusal=None),
            )
        ]
    )


def _message_item(text):
    return {"type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": text}]}


def _call_item(call_id, name, arguments):
    return {"type": "function_call", "id": "fc_" + call_id, "call_id": call_id,
            "name": name, "arguments": arguments}


def _reasoning_item(item_id="rs_1", blob="ENCRYPTED"):
    return {"type": "reasoning", "id": item_id, "summary": [], "encrypted_content": blob}


def _response(output, status="completed", incomplete_reason=None):
    response = {
        "status": status,
        "output": output,
        "usage": {"input_tokens": 7, "output_tokens": 4, "total_tokens": 11,
                  "output_tokens_details": {"reasoning_tokens": 2}},
    }
    if incomplete_reason:
        response["incomplete_details"] = {"reason": incomplete_reason}
    return response


def _bad_request(message, param=None, code=None):
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    body = {"error": {"message": message, "param": param, "code": code,
                      "type": "invalid_request_error"}}
    return BadRequestError(message, response=httpx.Response(400, request=request), body=body)


@tagged("post_install", "-at_install", "llm_openai")
class TestOpenAIChatRouting(TransactionCase):
    def setUp(self):
        super().setUp()
        self.provider = self.env["llm.provider"].create({
            "name": "OpenAI Routing Test",
            "service": "openai",
            "api_key": FAKE_KEY,
        })
        self.calls = []
        self.responses_queue = []
        self.client = SimpleNamespace(
            responses=SimpleNamespace(create=self._fake_responses),
            chat=SimpleNamespace(completions=SimpleNamespace(create=self._fake_completions)),
        )
        self.patch(type(self.env["llm.provider"]), "openai_get_client", lambda record: self.client)
        self.tool = self.env["llm.tool"].create({
            "name": "lookup_patient_count",
            "description": "Count records.",
            "implementation": "function",
            "input_schema": json.dumps({
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            }),
        })

    # ------------------------------------------------------------------
    def _fake_responses(self, **params):
        self.calls.append((RESPONSES, params))
        return self._next()

    def _fake_completions(self, **params):
        self.calls.append((CHAT_COMPLETIONS, params))
        return self._next()

    def _next(self):
        result = self.responses_queue.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def _model(self, name, model_use="chat", **values):
        return self.env["llm.model"].create(dict(
            name=name, provider_id=self.provider.id, model_use=model_use, **values
        ))

    def _chat(self, model, stream=False, tools=None, history=None, **kwargs):
        result = self.provider.chat(
            self.env["mail.message"],
            model=model,
            stream=stream,
            tools=tools,
            prepend_messages=history or [{"role": "user", "content": "Hello"}],
            **kwargs,
        )
        return list(result) if stream else result

    # ------------------------------------------------------------------
    # Capability registry
    # ------------------------------------------------------------------
    def test_registry_classifies_account_models(self):
        self.assertEqual(lookup("gpt-4o-2024-08-06").base_id, "gpt-4o")
        self.assertTrue(lookup("gpt-4o-2024-08-06").verified)
        self.assertEqual(lookup("gpt-5.5-pro").endpoints, (RESPONSES,))
        self.assertFalse(lookup("gpt-5.5-pro").streaming)
        for name in ("gpt-image-1", "tts-1", "gpt-realtime", "omni-moderation-latest",
                     "gpt-5-search-api", "dall-e-3"):
            self.assertFalse(lookup(name).chat_capable, name)
        # Audio chat models are Chat Completions-only (MH-1329 analyze-audio).
        audio = lookup("gpt-audio")
        self.assertTrue(audio.chat_capable)
        self.assertTrue(audio.direct_audio_chat)
        self.assertEqual(audio.endpoints, (CHAT_COMPLETIONS,))
        self.assertFalse(audio.supports_endpoint(RESPONSES))
        self.assertEqual(lookup("text-embedding-3-small").kind, "embedding")
        self.assertEqual(lookup("gpt-4o-transcribe").kind, "transcription")
        self.assertFalse(lookup("gpt-7-nova").verified)
        self.assertFalse(lookup("totally-new-model").chat_capable)

    def test_discovery_skips_unusable_kinds_and_flags_unknown(self):
        listed = [
            SimpleNamespace(id=name, model_dump=lambda name=name: {"id": name, "object": "model"})
            for name in ("gpt-4o", "gpt-image-1", "tts-1", "gpt-6.2-sol", "gpt-7-nova")
        ]
        self.client.models = SimpleNamespace(list=lambda: SimpleNamespace(data=listed))
        parsed = {row["name"]: row for row in self.provider.openai_models()}
        self.assertEqual(set(parsed), {"gpt-4o", "gpt-6.2-sol", "gpt-7-nova"})
        uses = {
            name: self.provider._determine_model_use(name, row["details"]["capabilities"])
            for name, row in parsed.items()
        }
        self.assertEqual(uses["gpt-4o"], "multimodal")
        self.assertIn(uses["gpt-6.2-sol"], ("chat", "multimodal"))
        self.assertEqual(uses["gpt-7-nova"], "unverified")

    # ------------------------------------------------------------------
    # Endpoint routing
    # ------------------------------------------------------------------
    def test_gpt4_stays_on_chat_completions(self):
        self.responses_queue.append(_cc_response("Hi there"))
        result = self._chat(self._model("gpt-4o-mini", "multimodal"), temperature=0.3, max_tokens=50)
        endpoint, params = self.calls[0]
        self.assertEqual(endpoint, CHAT_COMPLETIONS)
        self.assertEqual(params["temperature"], 0.3)
        self.assertEqual(params["max_completion_tokens"], 50)
        self.assertNotIn("reasoning_effort", params)
        self.assertEqual(result["content"], "Hi there")

    def test_reasoning_model_routes_to_responses_and_filters_params(self):
        self.responses_queue.append(_response([_reasoning_item(), _message_item("Done")]))
        model = self._model("gpt-5-mini", openai_reasoning_effort="low", openai_max_output_tokens=300)
        result = self._chat(model, temperature=0.7, top_p=0.9, logprobs=True)
        endpoint, params = self.calls[0]
        self.assertEqual(endpoint, RESPONSES)
        self.assertFalse(params["store"])
        self.assertEqual(params["include"], ["reasoning.encrypted_content"])
        self.assertEqual(params["reasoning"], {"effort": "low"})
        self.assertEqual(params["max_output_tokens"], 300)
        for name in ("temperature", "top_p", "logprobs", "max_tokens"):
            self.assertNotIn(name, params)
        self.assertEqual(result["content"], "Done")
        self.assertEqual(result["finish_reason"], "stop")
        self.assertEqual(result["usage"]["reasoning_tokens"], 2)
        state = result["provider_state"]["openai"]
        self.assertEqual(state["items"][0]["encrypted_content"], "ENCRYPTED")

    def test_gpt6_tool_call_uses_responses(self):
        self.responses_queue.append(_response([_call_item("call_1", "lookup_patient_count", '{"city":"X"}')]))
        result = self._chat(self._model("gpt-6.2-sol"), tools=self.tool)
        endpoint, params = self.calls[0]
        self.assertEqual(endpoint, RESPONSES)
        self.assertEqual(params["tools"][0]["type"], "function")
        self.assertEqual(params["tools"][0]["name"], "lookup_patient_count")
        self.assertIs(params["tools"][0]["strict"], False)
        self.assertEqual(params["tool_choice"], "auto")
        self.assertEqual(result["tool_calls"][0]["id"], "call_1")
        self.assertEqual(result["finish_reason"], "tool_calls")

    def test_forced_chat_completions_with_tools_is_rejected_not_dropped(self):
        model = self._model("gpt-6.2-sol", openai_endpoint="chat_completions")
        with self.assertRaises(OpenAIChatError) as caught:
            self._chat(model, tools=self.tool)
        self.assertEqual(caught.exception.details["code"], "unsupported_tools")
        self.assertIn("gpt-6.2-sol", str(caught.exception))
        self.assertFalse(self.calls)

    def test_effort_none_models_allow_cc_tools_only_with_effort_none(self):
        self.responses_queue.append(_cc_response(tool_calls=[("c1", "lookup_patient_count", "{}")],
                                                 finish_reason="tool_calls"))
        model = self._model("gpt-5.4", openai_endpoint="chat_completions")
        result = self._chat(model, tools=self.tool)
        self.assertEqual(self.calls[0][0], CHAT_COMPLETIONS)
        self.assertEqual(result["tool_calls"][0]["id"], "c1")
        model.openai_reasoning_effort = "high"
        with self.assertRaises(OpenAIChatError) as caught:
            self._chat(model, tools=self.tool)
        self.assertIn("'none'", str(caught.exception))

    def test_responses_only_model_rejects_forced_chat_completions(self):
        with self.assertRaises(OpenAIChatError) as caught:
            self._chat(self._model("gpt-5-pro", openai_endpoint="chat_completions"))
        self.assertEqual(caught.exception.details["code"], "unsupported_endpoint")

    def test_invalid_reasoning_effort_is_actionable(self):
        with self.assertRaises(OpenAIChatError) as caught:
            self._chat(self._model("gpt-5-mini"), reasoning_effort="max")
        self.assertEqual(caught.exception.details["param"], "reasoning_effort")
        self.assertIn("minimal", str(caught.exception))

    def test_non_chat_and_unverified_models_are_refused(self):
        with self.assertRaises(OpenAIChatError) as caught:
            self._chat(self._model("gpt-image-1", "unsupported"))
        self.assertEqual(caught.exception.details["code"], "unsupported_model")
        with self.assertRaises(OpenAIChatError) as caught:
            self._chat(self._model("gpt-7-nova", "unverified"))
        self.assertEqual(caught.exception.details["code"], "unverified_model")
        self.assertFalse(self.calls)

    def test_probe_verifies_unknown_model_for_chat(self):
        model = self._model("gpt-7-nova", "unverified")

        def responses(**params):
            self.calls.append((RESPONSES, params))
            if "temperature" in params:
                raise _bad_request("no", param="temperature", code="unsupported_parameter")
            if params.get("tools"):
                return {"output": [_call_item("p1", "odoo_capability_probe", '{"ok":true}')]}
            return {"output": [_message_item("OK")]}

        def completions(**params):
            raise _bad_request("no", param="model", code="unsupported_model")

        self.client.responses.create = lambda **p: SimpleNamespace(**self._probe_dict(responses(**p)))
        self.client.chat.completions.create = completions
        model.action_openai_probe_capabilities()
        self.assertEqual(model.model_use, "multimodal")
        capabilities = model._openai_capabilities()
        self.assertTrue(capabilities.verified)
        self.assertEqual(capabilities.endpoints, (RESPONSES,))
        self.assertTrue(capabilities.reasoning)
        self.assertFalse(capabilities.sampling_allowed(RESPONSES))
        # A later re-import does not reset the verified model to unverified.
        model.write({"model_use": "unverified"})
        self.assertEqual(model.model_use, "multimodal")

    @staticmethod
    def _probe_dict(response):
        return {"output": [SimpleNamespace(**item) for item in response["output"]]}

    def test_compatible_host_keeps_legacy_chat_completions(self):
        self.provider.api_base = "http://localhost:11434/v1"
        self.responses_queue.append(_cc_response("legacy"))
        result = self._chat(self._model("llama3"), temperature=0.2)
        endpoint, params = self.calls[0]
        self.assertEqual(endpoint, CHAT_COMPLETIONS)
        self.assertEqual(result["content"], "legacy")
        self.assertNotIn("temperature", params)

    # ------------------------------------------------------------------
    # Tools, history and replay
    # ------------------------------------------------------------------
    def test_invalid_tool_name_is_rejected(self):
        self.tool.name = "bad name!"
        with self.assertRaises(OpenAIChatError) as caught:
            self._chat(self._model("gpt-5-mini"), tools=self.tool)
        self.assertEqual(caught.exception.details["code"], "invalid_tool_name")

    def test_history_replay_preserves_call_id_pairing_and_reasoning_order(self):
        history = [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": [
                {"type": "text", "text": "What is in this image?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ]},
            {
                "role": "assistant",
                "content": "Checking.",
                "tool_calls": [
                    {"id": "call_a", "type": "function",
                     "function": {"name": "lookup_patient_count", "arguments": '{"city":"A"}'}},
                    {"id": "call_b", "type": "function",
                     "function": {"name": "lookup_patient_count", "arguments": '{"city":"B"}'}},
                ],
                REPLAY_KEY: {
                    "model": "gpt-5-mini",
                    "text": "Checking.",
                    "text_matches_body": True,
                    "items": [
                        {"type": "reasoning", "id": "rs_9", "encrypted_content": "BLOB"},
                        {"type": "message"},
                        {"type": "function_call", "call_id": "call_a"},
                        {"type": "function_call", "call_id": "call_b"},
                    ],
                },
            },
            {"role": "tool", "tool_call_id": "call_a", "content": '{"count": 1}'},
            {"role": "tool", "tool_call_id": "call_b", "content": '{"count": 2}'},
        ]
        items = messages_to_input(history, "gpt-5-mini")
        self.assertEqual(
            [item.get("type") + ":" + item.get("role", item.get("call_id", "")) for item in items],
            ["message:developer", "message:user", "reasoning:", "message:assistant",
             "function_call:call_a", "function_call:call_b",
             "function_call_output:call_a", "function_call_output:call_b"],
        )
        self.assertEqual(items[1]["content"][1]["type"], "input_image")
        self.assertNotIn("id", items[4])

        # Another model never receives this model's encrypted reasoning.
        other = messages_to_input(history, "gpt-6.2-sol")
        self.assertNotIn("reasoning", [item["type"] for item in other])

        # An edited assistant body drops the reasoning that produced the old text.
        history[2][REPLAY_KEY]["text_matches_body"] = False
        history[2]["content"] = "Edited by user."
        edited = messages_to_input(history, "gpt-5-mini")
        self.assertNotIn("reasoning", [item["type"] for item in edited])
        self.assertEqual(edited[2]["content"][0]["text"], "Edited by user.")

    def test_orphan_tool_result_is_an_error(self):
        with self.assertRaises(OpenAIChatError):
            messages_to_input([{"role": "tool", "tool_call_id": "nope", "content": "{}"}], "gpt-5-mini")

    def test_chat_completions_payload_never_carries_replay_state(self):
        self.responses_queue.append(_cc_response("ok"))
        history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello", REPLAY_KEY: {"model": "x", "items": []}},
            {"role": "user", "content": "again"},
        ]
        self._chat(self._model("gpt-4.1-mini"), history=history)
        for message in self.calls[0][1]["messages"]:
            self.assertNotIn(REPLAY_KEY, message)

    def test_mail_message_replay_state_and_edit_detection(self):
        partner = self.env["res.partner"].create({"name": "Replay"})
        message = partner.message_post(
            body="Answer", subtype_xmlid="llm.mt_assistant", message_type="comment",
        )
        message.sudo().llm_provider_state = {"openai": {
            "model": "gpt-5-mini", "endpoint": "responses", "text": "Answer",
            "items": [{"type": "reasoning", "id": "rs_1", "encrypted_content": "B"}, {"type": "message"}],
        }}
        formatted = message.openai_format_message()
        self.assertTrue(formatted[REPLAY_KEY]["text_matches_body"])
        message.body = "<p>Changed</p>"
        self.assertFalse(message.openai_format_message()[REPLAY_KEY]["text_matches_body"])

    # ------------------------------------------------------------------
    # Normalization
    # ------------------------------------------------------------------
    def test_refusal_is_returned_as_content(self):
        self.responses_queue.append(_response([{
            "type": "message", "role": "assistant",
            "content": [{"type": "refusal", "refusal": "I can't help with that."}],
        }]))
        result = self._chat(self._model("gpt-5-mini"))
        self.assertEqual(result["content"], "I can't help with that.")
        self.assertEqual(result["finish_reason"], "refusal")

    def test_incomplete_response_with_tool_calls_is_an_error(self):
        self.responses_queue.append(_response(
            [_call_item("call_1", "lookup_patient_count", '{"ci')],
            status="incomplete", incomplete_reason="max_output_tokens",
        ))
        with self.assertRaises(OpenAIChatError) as caught:
            self._chat(self._model("gpt-5-mini"), tools=self.tool)
        self.assertEqual(caught.exception.details["param"], "max_output_tokens")

    def test_failed_response_is_an_error(self):
        response = _response([], status="failed")
        response["error"] = {"code": "server_error", "message": "boom"}
        self.responses_queue.append(response)
        with self.assertRaises(OpenAIChatError) as caught:
            self._chat(self._model("gpt-5-mini"))
        self.assertEqual(caught.exception.details["code"], "server_error")

    def test_api_error_is_normalized_and_redacted(self):
        self.responses_queue.append(_bad_request(
            "Unsupported value for key %s***" % FAKE_KEY[:6], param="reasoning.effort",
            code="unsupported_value",
        ))
        with self.assertLogs("odoo.addons.llm_openai.models.openai_provider", "WARNING") as logs:
            with self.assertRaises(OpenAIChatError) as caught:
                self._chat(self._model("gpt-5-mini"))
        text = str(caught.exception)
        self.assertIn("gpt-5-mini", text)
        self.assertIn("/v1/responses", text)
        self.assertIn("reasoning.effort", text)
        self.assertIn("unsupported_value", text)
        self.assertNotIn(FAKE_KEY, text)
        self.assertNotIn(FAKE_KEY, "\n".join(logs.output))
        self.assertNotIn("Unsupported value", "\n".join(logs.output))

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------
    def test_responses_stream_text_and_multiple_tool_calls(self):
        final = _response([
            _reasoning_item(),
            _message_item("Looking up."),
            _call_item("call_1", "lookup_patient_count", '{"city":"A"}'),
            _call_item("call_2", "lookup_patient_count", '{"city":"B"}'),
        ])
        self.responses_queue.append(iter([
            {"type": "response.created"},
            {"type": "response.output_text.delta", "delta": "Looking "},
            {"type": "response.output_text.delta", "delta": "up."},
            {"type": "response.function_call_arguments.delta", "delta": '{"ci'},
            {"type": "response.completed", "response": final},
        ]))
        chunks = self._chat(self._model("gpt-6.2-sol"), stream=True, tools=self.tool)
        self.assertTrue(self.calls[0][1]["stream"])
        text = "".join(chunk.get("content", "") for chunk in chunks)
        self.assertEqual(text, "Looking up.")
        tool_chunks = [chunk["tool_calls"] for chunk in chunks if chunk.get("tool_calls")]
        self.assertEqual(len(tool_chunks), 1)
        self.assertEqual([call["id"] for call in tool_chunks[0]], ["call_1", "call_2"])
        tail = chunks[-1]
        self.assertEqual(tail["finish_reason"], "tool_calls")
        self.assertEqual(tail["provider_state"]["openai"]["text"], "Looking up.")

    def test_interrupted_responses_stream_runs_no_tool(self):
        self.responses_queue.append(iter([
            {"type": "response.output_text.delta", "delta": "Par"},
            {"type": "response.output_item.added", "item": {"type": "function_call"}},
        ]))
        received = []
        with self.assertRaises(OpenAIChatError) as caught:
            for chunk in self.provider.chat(
                self.env["mail.message"], model=self._model("gpt-5-mini"), stream=True,
                tools=self.tool, prepend_messages=[{"role": "user", "content": "hi"}],
            ):
                received.append(chunk)
        self.assertEqual(caught.exception.details["code"], "stream_interrupted")
        self.assertFalse([chunk for chunk in received if chunk.get("tool_calls")])

    def test_chat_completions_stream_assembles_parallel_tool_calls(self):
        self.responses_queue.append(iter([
            _cc_chunk(tool_calls=[(0, "c1", "lookup_patient_count", '{"ci')]),
            _cc_chunk(tool_calls=[(1, "c2", "lookup_patient_count", '{"city":"B"}')]),
            _cc_chunk(tool_calls=[(0, None, None, 'ty":"A"}')]),
            _cc_chunk(finish_reason="tool_calls"),
        ]))
        chunks = self._chat(self._model("gpt-4.1-mini"), stream=True, tools=self.tool)
        calls = [chunk["tool_calls"] for chunk in chunks if chunk.get("tool_calls")][0]
        self.assertEqual([call["id"] for call in calls], ["c1", "c2"])
        self.assertEqual(calls[0]["function"]["arguments"], '{"city":"A"}')

    def test_interrupted_chat_completions_stream_runs_no_tool(self):
        self.responses_queue.append(iter([
            _cc_chunk(tool_calls=[(0, "c1", "lookup_patient_count", '{"ci')]),
        ]))
        received = []
        with self.assertRaises(OpenAIChatError):
            for chunk in self.provider.chat(
                self.env["mail.message"], model=self._model("gpt-4.1-mini"), stream=True,
                tools=self.tool, prepend_messages=[{"role": "user", "content": "hi"}],
            ):
                received.append(chunk)
        self.assertFalse([chunk for chunk in received if chunk.get("tool_calls")])

    def test_non_streaming_model_feeds_streaming_caller(self):
        self.responses_queue.append(_response([_message_item("Pro answer")]))
        chunks = self._chat(self._model("gpt-5.5-pro"), stream=True)
        self.assertNotIn("stream", self.calls[0][1])
        self.assertEqual(chunks[0]["content"], "Pro answer")
