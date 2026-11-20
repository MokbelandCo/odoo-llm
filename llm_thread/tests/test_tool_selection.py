import json

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged


class ToolSelectionCase(TransactionCase):
    """Tools with every Default / Always Include / active combination."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        services = cls.env["llm.provider"]._selection_service()
        if not services:
            raise cls.failureException("No LLM provider service is available")
        cls.provider = cls.env["llm.provider"].create(
            {"name": "Tool Selection Provider", "service": services[0][0]}
        )
        cls.model = cls.env["llm.model"].create(
            {
                "name": "tool-selection-model",
                "provider_id": cls.provider.id,
                "model_use": "chat",
            }
        )
        Tool = cls.env["llm.tool"].with_context(active_test=False)
        # Shipped tools must not leak into the expectations below.
        Tool.search([]).write({"default": False, "always_include": "no"})

        def tool(name, **vals):
            if (
                vals.get("always_include") in ("portal", "authenticated", "always")
                and "portal_ai_safe" in Tool._fields
            ):
                vals.setdefault("portal_ai_safe", True)
            return Tool.create(
                {
                    "name": name,
                    "description": name,
                    "implementation": "function",
                    **vals,
                }
            )

        cls.default_a = tool("sel_default_a", default=True)
        cls.default_b = tool("sel_default_b", default=True)
        cls.optional_c = tool("sel_optional_c")
        cls.always_d = tool("sel_always_d", always_include="always")
        cls.both_e = tool("sel_both_e", default=True, always_include="always")
        cls.inactive_default = tool("sel_inactive_default", default=True, active=False)
        cls.inactive_always = tool(
            "sel_inactive_always", always_include="always", active=False
        )
        cls.technical_always = tool(
            "sel_technical_always", always_include="always", category="technical"
        )

    def _set_always_include(self, tool, value, **extra):
        vals = dict(extra, always_include=value)
        if value in ("portal", "authenticated", "always") and "portal_ai_safe" in tool._fields:
            vals.setdefault("portal_ai_safe", True)
        tool.write(vals)

    def _thread(self, env=None, **vals):
        env = env or self.env
        return env["llm.thread"].create(
            {
                "name": "Tool selection thread",
                "provider_id": self.provider.id,
                "model_id": self.model.id,
                **vals,
            }
        )


@tagged("post_install", "-at_install")
class TestThreadToolSelection(ToolSelectionCase):
    def test_new_thread_preselects_active_default_optional_tools(self):
        thread = self._thread()
        self.assertEqual(thread.tool_ids, self.default_a | self.default_b)

    def test_default_get_preselects_for_forms(self):
        defaults = self.env["llm.thread"].default_get(["tool_ids"])
        ids = defaults["tool_ids"][0][2] if defaults["tool_ids"] else []
        self.assertCountEqual(ids, (self.default_a | self.default_b).ids)

    def test_explicit_empty_and_custom_lists_are_kept(self):
        self.assertFalse(self._thread(tool_ids=[(6, 0, [])]).tool_ids)
        self.assertFalse(self._thread(tool_ids=[]).tool_ids)
        custom = self._thread(tool_ids=[(6, 0, self.optional_c.ids)])
        self.assertEqual(custom.tool_ids, self.optional_c)

    def test_removed_default_is_not_reselected(self):
        thread = self._thread()
        thread.write({"tool_ids": [(3, self.default_a.id), (4, self.optional_c.id)]})
        self.optional_c.default = True
        self.default_a.default = True
        thread.invalidate_recordset()
        self.assertEqual(thread.tool_ids, self.default_b | self.optional_c)
        self.assertNotIn(self.default_a, thread._get_effective_tools())

    def test_effective_tools_union_always_include_once(self):
        thread = self._thread(tool_ids=[(6, 0, (self.default_a | self.both_e).ids)])
        effective = thread._get_effective_tools()
        self.assertEqual(
            set(effective.ids),
            {
                self.default_a.id,
                self.both_e.id,
                self.always_d.id,
                self.technical_always.id,
            },
        )
        self.assertEqual(len(effective.ids), len(set(effective.ids)))

    def test_always_include_changes_reach_existing_threads(self):
        thread = self._thread()
        self.assertIn(self.always_d, thread._get_effective_tools())
        self.always_d.always_include = "no"
        self.assertNotIn(self.always_d, thread._get_effective_tools())
        self._set_always_include(self.optional_c, "always")
        self.assertIn(self.optional_c, thread._get_effective_tools())

    def test_inactive_tools_never_effective_even_with_stale_ids(self):
        thread = self._thread(
            tool_ids=[(6, 0, (self.default_a | self.inactive_default).ids)]
        )
        for env_thread in (thread, thread.with_context(active_test=False)):
            effective = env_thread._get_effective_tools()
            self.assertNotIn(self.inactive_default, effective)
            self.assertNotIn(self.inactive_always, effective)
        self.default_a.active = False
        self.always_d.active = False
        effective = thread._get_effective_tools()
        self.assertNotIn(self.default_a, effective)
        self.assertNotIn(self.always_d, effective)

        self.default_a.active = True
        self.always_d.active = True
        self.inactive_always.active = True
        effective = thread._get_effective_tools()
        self.assertIn(self.default_a, effective, "still selected, so it resumes")
        self.assertIn(self.always_d, effective)
        self.assertIn(self.inactive_always, effective)

    def test_reactivated_optional_tool_only_if_still_selected(self):
        thread = self._thread()
        self.default_b.active = False
        thread.write({"tool_ids": [(3, self.default_b.id)]})
        self.default_b.active = True
        self.assertNotIn(self.default_b, thread._get_effective_tools())

    def test_inactive_and_always_include_are_not_new_defaults(self):
        defaults = self.env["llm.tool"]._get_default_optional_tools()
        self.assertEqual(defaults, self.default_a | self.default_b)
        self.assertNotIn(self.both_e, defaults)

    def test_always_include_matches_the_thread_user(self):
        Tool = self.env["llm.tool"]
        def audience_tool(name, value):
            vals = {
                "name": name,
                "description": name,
                "implementation": "function",
                "always_include": value,
            }
            if value in ("portal", "authenticated", "always") and "portal_ai_safe" in Tool._fields:
                vals["portal_ai_safe"] = True
            return Tool.create(vals)

        internal = audience_tool("sel_audience_internal", "internal")
        portal = audience_tool("sel_audience_portal", "portal")
        authenticated = audience_tool("sel_audience_authenticated", "authenticated")
        always = self.always_d | self.both_e | self.technical_always

        staff = self._thread(tool_ids=[(6, 0, [])])
        effective = staff._get_effective_tools()
        self.assertIn(internal, effective)
        self.assertIn(authenticated, effective)
        self.assertTrue(always <= effective)
        self.assertNotIn(portal, effective)

        portal_user = new_test_user(
            self.env, login="tool_selection_portal", groups="base.group_portal"
        )
        portal_thread = self._thread(user_id=portal_user.id, tool_ids=[(6, 0, [])])
        effective = portal_thread._get_effective_tools()
        self.assertIn(portal, effective)
        self.assertIn(authenticated, effective)
        self.assertTrue(always <= effective)
        self.assertNotIn(internal, effective)

        public_thread = self._thread(
            user_id=self.env.ref("base.public_user").id, tool_ids=[(6, 0, [])]
        )
        effective = public_thread._get_effective_tools()
        self.assertIn(portal, effective)
        self.assertIn(authenticated, effective)
        self.assertTrue(always <= effective)
        self.assertNotIn(internal, effective)

    def test_record_rules_apply_to_always_include_tools(self):
        user = new_test_user(
            self.env, login="tool_selection_user", groups="base.group_user"
        )
        thread = self._thread(env=self.env(user=user), user_id=user.id)
        self.assertEqual(thread.tool_ids, self.default_a | self.default_b)
        effective = thread._get_effective_tools()
        self.assertIn(self.always_d, effective)
        self.assertNotIn(self.technical_always, effective)


@tagged("post_install", "-at_install")
class TestThreadToolExecutionGuard(ToolSelectionCase):
    """Tool execution resolves tools through the same effective set."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.retriever = cls.env.ref("llm_tool.llm_tool_odoo_record_retriever")
        cls.partner = cls.env["res.partner"].create({"name": "Tool Guard Partner"})

    def _call(self, tool, thread):
        args = json.dumps(
            {
                "model": "res.partner",
                "domain": [["id", "=", self.partner.id]],
                "fields": ["name"],
            }
        )
        return self.env["mail.message"]._execute_tool_with_context(
            tool.name, args, thread
        )

    def test_always_include_tool_executes_without_selection(self):
        self._set_always_include(self.retriever, "always")
        thread = self._thread(tool_ids=[(6, 0, [])])
        result = self._call(self.retriever, thread)
        self.assertIn("Tool Guard Partner", json.dumps(result, default=str))

    def test_inactive_selected_tool_cannot_execute(self):
        thread = self._thread(tool_ids=[(6, 0, self.retriever.ids)])
        self.assertTrue(self._call(self.retriever, thread))
        self.retriever.active = False
        with self.assertRaisesRegex(UserError, "not found in thread"):
            self._call(self.retriever, thread)
        tool_call = {
            "id": "call_stale",
            "type": "function",
            "function": {"name": self.retriever.name, "arguments": "{}"},
        }
        with self.assertRaisesRegex(UserError, "not found in thread"):
            self.env["mail.message"].post_tool_call(tool_call, thread_model=thread)

    def test_unselected_optional_tool_cannot_execute(self):
        thread = self._thread(tool_ids=[(6, 0, [])])
        with self.assertRaisesRegex(UserError, "not found in thread"):
            self._call(self.retriever, thread)
