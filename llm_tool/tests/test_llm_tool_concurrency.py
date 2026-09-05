"""
Test tool registration: in-memory scan + raw SQL sync.

_register_hook populates _tool_registry (no DB writes).
_sync_tools_to_db writes via raw SQL with advisory lock.
action_sync_tools wraps _sync_tools_to_db with UI notification.
"""

from odoo.tests import common


class TestLLMToolSync(common.TransactionCase):
    """Test the tool sync mechanism."""

    def setUp(self):
        super().setUp()
        self.LLMTool = self.env["llm.tool"]
        self.original_registry = dict(self.LLMTool._tool_registry)
        self.baseline_xml_keys = set(self.LLMTool._xml_managed_keys)
        # A registry that mirrors the function tools already in the DB. Tests
        # layer their own entries on top of it, so the sync only ever sees the
        # tools a test set up as added or missing; without the mirror, every
        # tool contributed by another installed addon would count as an orphan.
        self.baseline_registry = self._mirror_db_tools()
        self.addCleanup(self._set_registry, self.original_registry)
        self.addCleanup(self._set_xml_managed_keys, self.baseline_xml_keys)

    def _mirror_db_tools(self):
        """Build registry entries matching every function tool in the DB."""
        tools = self.LLMTool.with_context(active_test=False).search(
            [("implementation", "=", "function")]
        )
        return {
            (tool.decorator_model, tool.decorator_method): {
                "name": tool.name,
                "implementation": "function",
                "decorator_model": tool.decorator_model,
                "decorator_method": tool.decorator_method,
                "description": tool.description,
                "active": tool.active,
            }
            for tool in tools
        }

    def _set_registry(self, registry):
        """Replace the decorator registry in place.

        Model classes declare ``__slots__``, so the class attribute cannot be
        rebound through a recordset.
        """
        self.LLMTool._tool_registry.clear()
        self.LLMTool._tool_registry.update(registry)

    def _set_xml_managed_keys(self, keys):
        self.LLMTool._xml_managed_keys.clear()
        self.LLMTool._xml_managed_keys.update(keys)

    def _use_registry(self, entries=None, xml_keys=None):
        """Apply the mirrored baseline registry plus the given test entries."""
        self._set_registry({**self.baseline_registry, **(entries or {})})
        self._set_xml_managed_keys(self.baseline_xml_keys | (xml_keys or set()))

    def _registry_entry(self, name, method, model="res.partner", **values):
        """Build a registry mapping for one tool, matching _create_tool."""
        return {
            (model, method): {
                "name": name,
                "implementation": "function",
                "decorator_model": model,
                "decorator_method": method,
                "description": f"Desc for {name}",
                "active": True,
                **values,
            }
        }

    def _create_keeper(self):
        """Create a tool that is present both in the DB and in the registry.

        ``_sync_tools_to_db`` short-circuits on an empty registry so that a
        failed decorator scan cannot deactivate every tool. The keeper makes
        the registry non-empty without adding any work for the sync, so
        deactivation tests exercise the real branch.
        """
        self._create_tool("keeper", method="keeper_method")
        return self._registry_entry("keeper", "keeper_method")

    def _create_tool(self, name, model="res.partner", method=None, **kw):
        """Helper: create a function tool in DB."""
        return self.LLMTool.create(
            {
                "name": name,
                "description": kw.pop("description", f"Desc for {name}"),
                "implementation": "function",
                "decorator_model": model,
                "decorator_method": method or name,
                **kw,
            }
        )

    # -- _sync_tools_to_db (raw SQL) --

    def test_sync_creates_new_tool(self):
        self._use_registry(
            {
                ("res.partner", "new_method"): {
                    "name": "new_tool",
                    "implementation": "function",
                    "decorator_model": "res.partner",
                    "decorator_method": "new_method",
                    "description": "A new tool",
                    "active": True,
                }
            }
        )

        result = self.LLMTool._sync_tools_to_db()

        self.assertEqual(result["created"], 1)
        tool = self.LLMTool.search([("name", "=", "new_tool")])
        self.assertTrue(tool)
        self.assertEqual(tool.description, "A new tool")

    def test_sync_updates_changed_tool(self):
        self._create_tool("upd_tool", method="upd_method", description="Old")

        self._use_registry(
            {
                ("res.partner", "upd_method"): {
                    "name": "upd_tool",
                    "implementation": "function",
                    "decorator_model": "res.partner",
                    "decorator_method": "upd_method",
                    "description": "New",
                    "active": True,
                }
            }
        )
        self.LLMTool.invalidate_model()

        result = self.LLMTool._sync_tools_to_db()

        self.assertEqual(result["updated"], 1)
        tool = self.LLMTool.search([("name", "=", "upd_tool")])
        self.assertEqual(tool.description, "New")

    def test_sync_noop_when_unchanged(self):
        self._create_tool("same_tool", method="same_method", description="Same")

        self._use_registry(
            {
                ("res.partner", "same_method"): {
                    "name": "same_tool",
                    "implementation": "function",
                    "decorator_model": "res.partner",
                    "decorator_method": "same_method",
                    "description": "Same",
                    "active": True,
                }
            }
        )
        self.LLMTool.invalidate_model()

        result = self.LLMTool._sync_tools_to_db()

        self.assertEqual(result["created"], 0)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["deactivated"], 0)

    def test_sync_deactivates_missing_tool(self):
        keeper = self._create_keeper()
        tool = self._create_tool("orphan", method="orphan_method")
        self._use_registry(keeper)
        self.LLMTool.invalidate_model()

        result = self.LLMTool._sync_tools_to_db()

        self.assertEqual(result["deactivated"], 1)
        tool.invalidate_recordset()
        self.assertFalse(tool.active)

    def test_sync_skips_xml_managed_deactivation(self):
        keeper = self._create_keeper()
        tool = self._create_tool("xml_tool", method="xml_method")
        self._use_registry(keeper, xml_keys={("res.partner", "xml_method")})
        self.LLMTool.invalidate_model()

        result = self.LLMTool._sync_tools_to_db()

        self.assertEqual(result["deactivated"], 0)
        tool.invalidate_recordset()
        self.assertTrue(tool.active)

    def test_sync_respects_auto_update_false(self):
        self._create_tool(
            "locked_tool",
            method="locked_method",
            description="Manual",
            auto_update=False,
        )
        self._use_registry(
            {
                ("res.partner", "locked_method"): {
                    "name": "locked_tool",
                    "implementation": "function",
                    "decorator_model": "res.partner",
                    "decorator_method": "locked_method",
                    "description": "Decorator says different",
                    "active": True,
                }
            }
        )
        self.LLMTool.invalidate_model()

        result = self.LLMTool._sync_tools_to_db()

        self.assertEqual(result["updated"], 0)
        tool = self.LLMTool.search([("name", "=", "locked_tool")])
        self.assertEqual(tool.description, "Manual")

    # -- action_sync_tools (button) --

    def test_action_empty_registry(self):
        self._set_registry({})
        result = self.LLMTool.action_sync_tools()
        self.assertEqual(result["params"]["type"], "warning")

    def test_action_already_in_sync(self):
        self._create_tool("btn_tool", method="btn_method", description="OK")
        self._use_registry(
            {
                ("res.partner", "btn_method"): {
                    "name": "btn_tool",
                    "implementation": "function",
                    "decorator_model": "res.partner",
                    "decorator_method": "btn_method",
                    "description": "OK",
                    "active": True,
                }
            }
        )
        self.LLMTool.invalidate_model()

        result = self.LLMTool.action_sync_tools()
        self.assertIn("Already in sync", result["params"]["title"])

    def test_action_reports_changes(self):
        self._use_registry(
            {
                ("res.partner", "action_new"): {
                    "name": "action_new_tool",
                    "implementation": "function",
                    "decorator_model": "res.partner",
                    "decorator_method": "action_new",
                    "description": "Via button",
                    "active": True,
                }
            }
        )

        result = self.LLMTool.action_sync_tools()
        self.assertEqual(result["params"]["type"], "success")

    # -- _register_hook & helpers --

    def test_register_hook_populates_registry(self):
        self.LLMTool._tool_registry.clear()
        self.LLMTool._xml_managed_keys.clear()
        self.LLMTool._register_hook()
        self.assertIsInstance(self.LLMTool._tool_registry, dict)
        self.assertIsInstance(self.LLMTool._xml_managed_keys, set)

    def test_extract_tool_values(self):
        def mock(self):
            """Mock desc"""

        mock._llm_tool_name = "my_tool"
        mock._llm_tool_description = "My tool"
        mock._llm_tool_metadata = {"read_only_hint": True, "idempotent_hint": True}

        values = self.LLMTool._extract_tool_values("res.partner", "mock", mock)

        self.assertEqual(values["name"], "my_tool")
        self.assertEqual(values["description"], "My tool")
        self.assertTrue(values["read_only_hint"])
        self.assertTrue(values["idempotent_hint"])
        self.assertNotIn("destructive_hint", values)

    def test_raw_values_changed_detects_difference(self):
        db_row = {"name": "old", "description": "old desc", "active": True}
        values = {
            "name": "old",
            "description": "new desc",
            "active": True,
        }
        self.assertTrue(self.LLMTool._raw_values_changed(db_row, values))

    def test_raw_values_changed_ignores_none_vs_empty(self):
        db_row = {"name": "t", "description": None, "active": True}
        values = {"name": "t", "description": "", "active": True}
        self.assertFalse(self.LLMTool._raw_values_changed(db_row, values))
