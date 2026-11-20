import json
from contextlib import contextmanager
from unittest.mock import patch

from odoo.tests import Form
from odoo.tests.common import new_test_user, tagged
from odoo.tools import mute_logger

from odoo.addons.llm_thread.tests.test_tool_selection import ToolSelectionCase


@tagged("post_install", "-at_install")
class TestAssistantToolSelection(ToolSelectionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.prompt = cls.env["llm.prompt"].create(
            {
                "name": "Tool Selection Prompt",
                "template": "You are a test assistant.",
                "format": "text",
            }
        )
        cls.retriever = cls.env.ref("llm_tool.llm_tool_odoo_record_retriever")
        cls.partner = cls.env["res.partner"].create({"name": "Tool Runtime Partner"})

    def _assistant(self, **vals):
        return self.env["llm.assistant"].create(
            {
                "name": "Tool Selection Assistant",
                "prompt_id": self.prompt.id,
                "provider_id": self.provider.id,
                "model_id": self.model.id,
                "is_public": True,
                **vals,
            }
        )

    # ------------------------------------------------------------------
    # Fake provider: records what each chat request offers the model.
    # ------------------------------------------------------------------

    @contextmanager
    def _fake_provider(self, replies, stream=True):
        """Answer chat requests with ``replies`` (``{"content"}`` or
        ``{"tool_calls"}``) and record the tools sent with each request.

        Only the service dispatch is replaced, so ``llm.provider.chat`` (consent
        injection, logging) runs for real.
        """
        Provider = type(self.env["llm.provider"])
        Model = type(self.env["llm.model"])
        original = Provider._dispatch
        requests = []
        replies = list(replies)

        def dispatch(provider, method, *args, record=None, **kwargs):
            if method == "normalize_prepend_messages":
                return args[0]
            if method != "chat":
                return original(provider, method, *args, record=record, **kwargs)
            tools = kwargs.get("tools") or self.env["llm.tool"]
            requests.append(
                {
                    "tools": tools.mapped("name"),
                    "stream": kwargs.get("stream"),
                    "prepend": kwargs.get("prepend_messages") or [],
                }
            )
            reply = replies.pop(0) if replies else {"content": "done"}
            if not kwargs.get("stream"):
                return reply

            def chunks():
                yield reply

            return chunks()

        with patch.object(Provider, "_dispatch", dispatch), patch.object(
            Model, "supports_streaming", stream, create=True
        ), patch.object(type(self.env.cr), "commit", lambda cr: None):
            yield requests

    def _retriever_call(self, call_id="call_runtime"):
        return {
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": self.retriever.name,
                        "arguments": json.dumps(
                            {
                                "model": "res.partner",
                                "domain": [["id", "=", self.partner.id]],
                                "fields": ["name"],
                            }
                        ),
                    },
                }
            ]
        }

    @staticmethod
    def _tool_messages(thread):
        return thread.message_ids.filtered(lambda m: m.llm_role == "tool")

    # ------------------------------------------------------------------
    # Assistant configuration
    # ------------------------------------------------------------------

    def test_new_assistant_preselects_default_optional_tools(self):
        self.assertEqual(self._assistant().tool_ids, self.default_a | self.default_b)
        self.assertFalse(self._assistant(tool_ids=[(6, 0, [])]).tool_ids)
        custom = self._assistant(tool_ids=[(6, 0, self.optional_c.ids)])
        self.assertEqual(custom.tool_ids, self.optional_c)

    def test_assistant_form_defaults_are_editable_and_kept(self):
        form = Form(self.env["llm.assistant"])
        form.name = "Form Assistant"
        form.prompt_id = self.prompt
        self.assertCountEqual(
            [tool.id for tool in form.tool_ids],
            (self.default_a | self.default_b).ids,
        )
        form.tool_ids.remove(id=self.default_a.id)
        form.tool_ids.add(self.optional_c)
        assistant = form.save()
        self.assertEqual(assistant.tool_ids, self.default_b | self.optional_c)
        self.assertNotIn(self.always_d, assistant.tool_ids)

        self.default_a.default = False
        self.optional_c.default = False
        self.retriever.default = True
        assistant.invalidate_recordset()
        self.assertEqual(assistant.tool_ids, self.default_b | self.optional_c)

    def test_thread_created_for_assistant_uses_its_tools(self):
        assistant = self._assistant(tool_ids=[(6, 0, self.optional_c.ids)])
        thread = self._thread(assistant_id=assistant.id)
        self.assertEqual(thread.tool_ids, self.optional_c)
        explicit = self._thread(assistant_id=assistant.id, tool_ids=[(6, 0, [])])
        self.assertFalse(explicit.tool_ids)

    # ------------------------------------------------------------------
    # Runtime: provider request and tool execution
    # ------------------------------------------------------------------

    def test_assistant_launch_runtime_uses_configured_plus_always_include(self):
        form = Form(self.env["llm.assistant"])
        form.name = "Launch Assistant"
        form.prompt_id = self.prompt
        form.provider_id = self.provider
        form.model_id = self.model
        form.is_public = True
        form.tool_ids.remove(id=self.default_a.id)
        form.tool_ids.add(self.optional_c)
        assistant = form.save()

        thread = self._thread()
        self.assertIn(self.default_a, thread.tool_ids)
        thread.set_assistant(assistant.id)
        self.assertEqual(thread.tool_ids, self.default_b | self.optional_c)

        for stream in (True, False):
            with self.subTest(stream=stream), self._fake_provider(
                [{"content": "hi"}], stream=stream
            ) as requests:
                list(thread.generate(user_message_body="hello"))
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0]["stream"], stream)
            self.assertCountEqual(
                requests[0]["tools"],
                [
                    self.default_b.name,
                    self.optional_c.name,
                    self.always_d.name,
                    self.both_e.name,
                    self.technical_always.name,
                ],
            )

    def test_always_include_tool_is_offered_once_and_callable(self):
        self._set_always_include(self.retriever, "always")
        assistant = self._assistant(tool_ids=[(6, 0, self.optional_c.ids)])
        for stream in (True, False):
            for with_assistant in (False, True):
                with self.subTest(stream=stream, assistant=with_assistant):
                    thread = self._thread(tool_ids=[(6, 0, self.retriever.ids)])
                    if with_assistant:
                        thread.set_assistant(assistant.id)
                    with self._fake_provider(
                        [self._retriever_call(), {"content": "found it"}],
                        stream=stream,
                    ) as requests:
                        list(thread.generate(user_message_body="find the partner"))
                    self.assertEqual(len(requests), 2)
                    for request in requests:
                        self.assertEqual(
                            request["tools"].count(self.retriever.name), 1
                        )
                    tool_message = self._tool_messages(thread)
                    self.assertEqual(len(tool_message), 1)
                    self.assertEqual(tool_message.body_json["status"], "completed")
                    self.assertIn(
                        "Tool Runtime Partner",
                        json.dumps(tool_message.body_json["result"], default=str),
                    )

    def test_always_include_toggle_changes_next_request(self):
        thread = self._thread()
        with self._fake_provider([{"content": "one"}]) as requests:
            list(thread.generate(user_message_body="first"))
        self.assertIn(self.always_d.name, requests[0]["tools"])

        self.always_d.always_include = "no"
        self._set_always_include(self.optional_c, "always")
        with self._fake_provider([{"content": "two"}]) as requests:
            list(thread.generate(user_message_body="second"))
        self.assertNotIn(self.always_d.name, requests[0]["tools"])
        self.assertIn(self.optional_c.name, requests[0]["tools"])

    def test_removed_default_stays_removed_across_requests(self):
        thread = self._thread()
        thread.write({"tool_ids": [(3, self.default_a.id)]})
        for text in ("first", "second"):
            with self._fake_provider([{"content": text}]) as requests:
                list(thread.generate(user_message_body=text))
            self.assertNotIn(self.default_a.name, requests[0]["tools"])
            self.assertIn(self.default_b.name, requests[0]["tools"])

    @mute_logger("odoo.addons.llm_assistant.models.llm_thread")
    def test_deactivated_tool_is_hidden_and_refused(self):
        for stream in (True, False):
            for flags in ({}, {"always_include": "always"}):
                with self.subTest(stream=stream, **flags):
                    self._set_always_include(self.retriever, "no", active=True)
                    thread = self._thread(tool_ids=[(6, 0, self.retriever.ids)])
                    if flags:
                        self._set_always_include(self.retriever, "always", active=False)
                    else:
                        self.retriever.write({"active": False})
                    with self._fake_provider(
                        [self._retriever_call(), {"content": "no tool"}],
                        stream=stream,
                    ) as requests:
                        list(thread.generate(user_message_body="stale id"))
                    self.assertNotIn(self.retriever.name, requests[0]["tools"])
                    tool_message = self._tool_messages(thread)
                    self.assertEqual(tool_message.body_json["status"], "error")
                    self.assertIn("not found in thread", tool_message.body_json["error"])
                    self.assertNotIn("result", tool_message.body_json)
        self._set_always_include(self.retriever, "always", active=True)
        thread = self._thread(tool_ids=[(6, 0, [])])
        with self._fake_provider([{"content": "back"}]) as requests:
            list(thread.generate(user_message_body="reactivated"))
        self.assertIn(self.retriever.name, requests[0]["tools"])

    def test_consent_instruction_covers_always_include_tools(self):
        self.always_d.requires_user_consent = True
        thread = self._thread(tool_ids=[(6, 0, [])])
        with self._fake_provider([{"content": "ok"}]) as requests:
            list(thread.generate(user_message_body="consent"))
        system_text = " ".join(
            str(msg.get("content"))
            for msg in requests[0]["prepend"]
            if msg.get("role") == "system"
        )
        self.assertIn(f"'{self.always_d.name}'", system_text)

    @mute_logger("odoo.addons.llm_assistant.models.llm_thread")
    def test_record_rules_limit_always_include_for_standard_users(self):
        user = new_test_user(
            self.env, login="tool_runtime_user", groups="base.group_user"
        )
        self._set_always_include(self.retriever, "always")
        thread = self._thread(env=self.env(user=user), user_id=user.id)
        with self._fake_provider(
            [
                {
                    "tool_calls": [
                        {
                            "id": "call_technical",
                            "type": "function",
                            "function": {
                                "name": self.technical_always.name,
                                "arguments": "{}",
                            },
                        }
                    ]
                },
                {"content": "refused"},
            ]
        ) as requests:
            list(thread.generate(user_message_body="technical"))
        self.assertIn(self.retriever.name, requests[0]["tools"])
        self.assertNotIn(self.technical_always.name, requests[0]["tools"])
        tool_message = self._tool_messages(thread)
        self.assertEqual(tool_message.body_json["status"], "error")
