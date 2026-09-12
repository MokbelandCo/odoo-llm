from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged

EMPTY_SCHEMA = '{"type": "object", "properties": {}}'


@tagged("post_install", "-at_install")
class TestMcpToolExposure(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Config = cls.env["llm.mcp.server.config"]
        cls.Tool = cls.env["llm.tool"]
        cls.config = cls.Config.get_active_config()
        cls.tool_a = cls.Tool.create(
            {
                "name": "mcp_exposure_tool_a",
                "description": "First test tool",
                "implementation": "function",
                "input_schema": EMPTY_SCHEMA,
                "decorator_model": "llm.mcp.server.config",
                "decorator_method": "mcp_exposure_tool_a",
            }
        )
        cls.tool_b = cls.Tool.create(
            {
                "name": "mcp_exposure_tool_b",
                "description": "Second test tool",
                "implementation": "function",
                "input_schema": EMPTY_SCHEMA,
                "decorator_model": "llm.mcp.server.config",
                "decorator_method": "mcp_exposure_tool_b",
            }
        )

    def test_default_mode_exposes_all_active_tools(self):
        self.config.tool_mode = "all"
        names = set(self.config.get_exposed_tools().mapped("name"))
        self.assertIn("mcp_exposure_tool_a", names)
        self.assertIn("mcp_exposure_tool_b", names)

        listed = {
            tool.name for tool in self.Tool.get_mcp_tools_list().tools
        }
        self.assertIn("mcp_exposure_tool_a", listed)
        self.assertIn("mcp_exposure_tool_b", listed)

    def test_selected_mode_lists_only_allowlisted_tools(self):
        self.config.write(
            {
                "tool_mode": "selected",
                "tool_ids": [(6, 0, [self.tool_a.id])],
            }
        )
        names = self.config.get_exposed_tools().mapped("name")
        self.assertEqual(names, ["mcp_exposure_tool_a"])

        listed = {
            tool.name for tool in self.Tool.get_mcp_tools_list().tools
        }
        self.assertIn("mcp_exposure_tool_a", listed)
        self.assertNotIn("mcp_exposure_tool_b", listed)

    def test_selected_mode_skips_inactive_allowlisted_tools(self):
        self.tool_a.active = False
        self.config.write(
            {
                "tool_mode": "selected",
                "tool_ids": [(6, 0, [self.tool_a.id, self.tool_b.id])],
            }
        )
        names = self.config.get_exposed_tools().mapped("name")
        self.assertEqual(names, ["mcp_exposure_tool_b"])

    def test_selected_mode_empty_allowlist_exposes_nothing(self):
        self.config.write({"tool_mode": "selected", "tool_ids": [(5, 0, 0)]})
        self.assertFalse(self.config.get_exposed_tools())
        self.assertFalse(self.Tool.get_mcp_tools_list().tools)

    def test_call_rejects_tools_outside_allowlist(self):
        self.config.write(
            {
                "tool_mode": "selected",
                "tool_ids": [(6, 0, [self.tool_a.id])],
            }
        )
        with self.assertRaises(UserError):
            self.Tool.execute_mcp_tool({"name": "mcp_exposure_tool_b", "arguments": {}})

    def test_only_one_config_can_be_active(self):
        extra = self.Config.create(
            {
                "name": "second_mcp_config",
                "version": "1.0.0",
                "latest_protocol_version": "2025-11-25",
                "active": False,
                "mode": "stateful",
                "tool_mode": "selected",
                "tool_ids": [(6, 0, [self.tool_b.id])],
            }
        )
        with self.assertRaises(ValidationError):
            extra.active = True

        self.config.write(
            {
                "tool_mode": "selected",
                "tool_ids": [(6, 0, [self.tool_a.id])],
            }
        )
        self.assertEqual(
            self.Config.get_active_config().get_exposed_tools().mapped("name"),
            ["mcp_exposure_tool_a"],
        )
        extra.unlink()

    def test_external_url_is_not_unique(self):
        url = "http://mcp.example.test:8069"
        self.config.external_url = url
        extra = self.Config.create(
            {
                "name": "duplicate_url_config",
                "version": "1.0.0",
                "latest_protocol_version": "2025-11-25",
                "active": False,
                "mode": "stateful",
                "external_url": url,
            }
        )
        duplicates = self.Config.search([("external_url", "=", url)])
        self.assertGreaterEqual(len(duplicates), 2)
        extra.unlink()
