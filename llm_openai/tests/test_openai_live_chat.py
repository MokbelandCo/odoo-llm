"""Live OpenAI chat smoke tests (opt-in).

Run only when ``OPENAI_API_KEY`` is set and the ``openai_live`` tag is
selected::

    --test-tags openai_live --test-enable

Models come from ``OPENAI_LIVE_MODELS`` (comma separated) or the default
list below. A model the account cannot use is recorded as skipped. When
``OPENAI_LIVE_REPORT`` names a file, one JSON row per check is written there
with the model, endpoint, check, outcome and a sanitized error only.
"""

import json
import os

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.llm_openai.utils.openai_capabilities import lookup
from odoo.addons.llm_openai.utils.openai_responses import REPLAY_KEY, redact

DEFAULT_MODELS = (
    "gpt-4o-mini",
    "gpt-4.1-mini",
    "gpt-4o",
    "chat-latest",
    "o3",
    "gpt-5-mini",
    "gpt-5.1",
    "gpt-5.4-mini",
    "gpt-5.5",
    "gpt-5.6-luna",
    "gpt-6-sol",
    "gpt-6.1-sol",
    "gpt-6.2-sol",
    "gpt-5.5-pro",
)
#: 16x16 solid red PNG.
RED_PNG = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAAF0lEQVR4"
    "nGP4z8BAEiJN9aiGUQ1DSgMAkPn/Afnh+ngAAAAASUVORK5CYII="
)
WEATHER_SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string", "description": "City name"}},
    "required": ["city"],
}


@tagged("-standard", "-at_install", "post_install", "openai_live")
class TestOpenAILiveChat(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.api_key = os.environ.get("OPENAI_API_KEY")
        cls.rows = []

    @classmethod
    def tearDownClass(cls):
        path = os.environ.get("OPENAI_LIVE_REPORT")
        if path and cls.rows:
            with open(path, "w", encoding="utf-8") as report:
                json.dump(cls.rows, report, indent=1)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        if not self.api_key:
            self.skipTest("OPENAI_API_KEY is not set")
        self.provider = self.env["llm.provider"].create({
            "name": "OpenAI Live Smoke",
            "service": "openai",
            "api_key": self.api_key,
        })
        self.tool = self.env["llm.tool"].create({
            "name": "get_city_temperature",
            "description": "Return the current temperature in Celsius for a city.",
            "implementation": "function",
            "input_schema": json.dumps(WEATHER_SCHEMA),
        })

    def _record(self, model, endpoint, check, outcome, error=None):
        row = {"model": model, "endpoint": endpoint, "check": check, "outcome": outcome}
        if error:
            row["error"] = redact(error, 240)
        self.rows.append(row)

    def _available(self, name):
        try:
            self.provider.client.models.retrieve(name)
            return True
        except Exception as error:  # noqa: BLE001
            self._record(name, None, "availability", "skipped", type(error).__name__)
            return False

    def _endpoint(self, model, tools):
        capabilities = model._openai_capabilities()
        options = self.provider._openai_chat_options(model, {})
        return self.provider._openai_select_endpoint(
            capabilities, options["endpoint"], tools, options.get("reasoning_effort")
        )

    def _run(self, model, check, stream, history, tools=None, **kwargs):
        result = model.provider_id.chat(
            self.env["mail.message"], model=model, stream=stream, tools=tools,
            prepend_messages=history, **kwargs,
        )
        if not stream:
            return result
        merged = {"content": "", "tool_calls": []}
        for chunk in result:
            merged["content"] += chunk.get("content") or ""
            merged["tool_calls"] += chunk.get("tool_calls") or []
            if chunk.get("provider_state"):
                merged["provider_state"] = chunk["provider_state"]
            if chunk.get("finish_reason"):
                merged["finish_reason"] = chunk["finish_reason"]
        return merged

    def _check(self, model, check, func):
        endpoint = None
        try:
            endpoint = self._endpoint(model, "tool" in check)
            func()
            self._record(model.name, endpoint, check, "pass")
        except Exception as error:  # noqa: BLE001
            self._record(model.name, endpoint, check, "fail", "%s: %s" % (type(error).__name__, error))
            raise

    def test_live_chat_models(self):
        names = [
            name.strip()
            for name in (os.environ.get("OPENAI_LIVE_MODELS") or ",".join(DEFAULT_MODELS)).split(",")
            if name.strip()
        ]
        for name in names:
            capabilities = lookup(name)
            if not self._available(name):
                continue
            model = self.env["llm.model"].create({
                "name": name,
                "provider_id": self.provider.id,
                "model_use": "multimodal" if capabilities.vision else "chat",
                "openai_max_output_tokens": 2000,
            })
            for stream in (False, True):
                mode = "stream" if stream else "non_stream"
                with self.subTest(model=name, check="text_" + mode):
                    self._check(model, "text_" + mode, lambda: self._text(model, stream))
                with self.subTest(model=name, check="tool_" + mode):
                    self._check(model, "tool_" + mode, lambda: self._tool_round_trip(model, stream))
            if capabilities.vision:
                with self.subTest(model=name, check="image"):
                    self._check(model, "image", lambda: self._image(model))

    def _text(self, model, stream):
        result = self._run(model, "text", stream, [
            {"role": "system", "content": "Answer in one word."},
            {"role": "user", "content": "What is the capital of France?"},
        ])
        self.assertIn("paris", (result.get("content") or "").lower())

    def _image(self, model):
        result = self._run(model, "image", False, [{"role": "user", "content": [
            {"type": "text", "text": "What single color fills this image? One word."},
            {"type": "image_url", "image_url": {"url": RED_PNG}},
        ]}])
        self.assertIn("red", (result.get("content") or "").lower())

    def _tool_round_trip(self, model, stream):
        history = [
            {"role": "system", "content": "Use the tool to answer weather questions."},
            {"role": "user", "content": "What is the temperature in Montreal right now?"},
        ]
        first = self._run(model, "tool", stream, history, tools=self.tool, tool_choice="required")
        calls = first.get("tool_calls") or []
        self.assertTrue(calls, "model returned no tool call")
        self.assertEqual(calls[0]["function"]["name"], "get_city_temperature")
        json.loads(calls[0]["function"]["arguments"] or "{}")
        assistant = {"role": "assistant", "content": first.get("content") or "", "tool_calls": calls}
        state = (first.get("provider_state") or {}).get("openai")
        if state:
            assistant[REPLAY_KEY] = dict(state, text_matches_body=True)
        history += [assistant] + [
            {"role": "tool", "tool_call_id": call["id"], "content": json.dumps({"celsius": 17})}
            for call in calls
        ]
        second = self._run(model, "tool", stream, history, tools=self.tool)
        self.assertIn("17", second.get("content") or "")
