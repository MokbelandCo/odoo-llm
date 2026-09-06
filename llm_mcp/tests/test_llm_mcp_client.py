import json
import os
import shlex
import sys

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.llm_mcp.models.llm_mcp_bus_manager import MCPBusManager

MOCK_SERVER = os.path.join(os.path.dirname(__file__), "mock_mcp_stdio_server.py")


@tagged("post_install", "-at_install")
class TestLLMMCPClient(TransactionCase):
    """Ported MCP client: stdio JSON-RPC against a local mock MCP server."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.MCPServer = cls.env["llm.mcp.server"]
        cls.LLMTool = cls.env["llm.tool"]

    def _cleanup_manager(self, server_id):
        MCPBusManager.discard(server_id)

    def _create_stdio_server(self, **vals):
        values = {
            "name": vals.get("name", "Test MCP Server"),
            "transport": "stdio",
            "command": shlex.quote(sys.executable),
            "args": shlex.quote(MOCK_SERVER),
            "is_active": True,
        }
        values.update(vals)
        server = self.MCPServer.create(values)
        self.addCleanup(self._cleanup_manager, server.id)
        return server

    def test_mcp_implementation_is_registered(self):
        selection = dict(self.LLMTool._selection_implementation())
        self.assertIn("mcp", selection)
        self.assertEqual(selection["mcp"], "MCP Client")

    def test_stdio_requires_command(self):
        with self.assertRaises(ValidationError):
            self.MCPServer.create(
                {
                    "name": "Missing Command",
                    "transport": "stdio",
                    "command": False,
                }
            )

    def test_internal_transport_start_stop_without_process(self):
        server = self.MCPServer.create(
            {
                "name": "Internal MCP",
                "transport": "internal",
            }
        )
        self.assertTrue(server.start_server())
        self.assertTrue(server.is_connected)
        result = server.execute_tool("anything", {})
        self.assertIn("error", result)
        self.assertTrue(server.stop_server())
        self.assertFalse(server.is_connected)

    def test_start_discovers_and_registers_tools(self):
        server = self._create_stdio_server()
        server.start_server()

        self.assertTrue(server.is_connected)
        self.assertEqual(server.protocol_version, "2025-03-26")
        self.assertIn("llm-mcp-test-server", server.server_info or "")

        echo = self.LLMTool.search(
            [("name", "=", "llm_mcp_test_echo"), ("mcp_server_id", "=", server.id)]
        )
        fail = self.LLMTool.search(
            [("name", "=", "llm_mcp_test_fail"), ("mcp_server_id", "=", server.id)]
        )
        self.assertEqual(len(echo), 1)
        self.assertEqual(len(fail), 1)
        self.assertEqual(echo.implementation, "mcp")
        self.assertEqual(echo.title, "Test Echo")
        self.assertTrue(echo.read_only_hint)
        self.assertTrue(echo.idempotent_hint)
        self.assertFalse(echo.destructive_hint)
        self.assertFalse(echo.open_world_hint)

        schema = echo.get_input_schema()
        self.assertEqual(schema["type"], "object")
        self.assertIn("message", schema["properties"])

    def test_execute_echo_tool(self):
        server = self._create_stdio_server()
        server.start_server()
        echo = self.LLMTool.search(
            [("name", "=", "llm_mcp_test_echo"), ("mcp_server_id", "=", server.id)],
            limit=1,
        )
        result = echo.execute({"message": "hello-mcp"})
        self.assertEqual(result, {"echoed": "hello-mcp"})

    def test_execute_skips_pydantic_signature(self):
        """Remote JSON args must not be validated against mcp_execute(**parameters)."""
        server = self._create_stdio_server()
        server.start_server()
        echo = self.LLMTool.search(
            [("name", "=", "llm_mcp_test_echo"), ("mcp_server_id", "=", server.id)],
            limit=1,
        )
        result = echo.execute({"message": "no-pydantic"})
        self.assertEqual(result["echoed"], "no-pydantic")

    def test_failing_tool_raises_user_error(self):
        server = self._create_stdio_server()
        server.start_server()
        fail = self.LLMTool.search(
            [("name", "=", "llm_mcp_test_fail"), ("mcp_server_id", "=", server.id)],
            limit=1,
        )
        with self.assertRaises(UserError) as ctx:
            fail.execute({})
        self.assertIn("intentional failure", str(ctx.exception))

    def test_inactive_server_blocks_execute(self):
        server = self._create_stdio_server()
        server.start_server()
        echo = self.LLMTool.search(
            [("name", "=", "llm_mcp_test_echo"), ("mcp_server_id", "=", server.id)],
            limit=1,
        )
        server.is_active = False
        with self.assertRaises(UserError) as ctx:
            echo.execute({"message": "blocked"})
        self.assertIn("not active", str(ctx.exception))

    def test_tool_without_server_raises(self):
        tool = self.LLMTool.create(
            {
                "name": "llm_mcp_orphan_tool",
                "description": "Orphan MCP tool",
                "implementation": "mcp",
            }
        )
        with self.assertRaises(UserError) as ctx:
            tool.execute({"message": "x"})
        self.assertIn("not associated", str(ctx.exception))

    def test_refresh_removes_stale_tools(self):
        server = self._create_stdio_server()
        server.start_server()
        stale = self.LLMTool.create(
            {
                "name": "llm_mcp_stale_tool",
                "description": "Should be removed on refresh",
                "implementation": "mcp",
                "mcp_server_id": server.id,
            }
        )
        stale_id = stale.id
        server.list_tools()
        self.assertFalse(self.LLMTool.browse(stale_id).exists())

    def test_stop_and_unlink_discards_process(self):
        server = self._create_stdio_server()
        server.start_server()
        server_id = server.id
        self.assertIn(f"server_{server_id}", MCPBusManager._instances)
        server.stop_server()
        self.assertFalse(server.is_connected)
        self.assertNotIn(f"server_{server_id}", MCPBusManager._instances)

        server.start_server()
        self.assertIn(f"server_{server_id}", MCPBusManager._instances)
        server.unlink()
        self.assertNotIn(f"server_{server_id}", MCPBusManager._instances)

    def test_reset_input_schema_refetches_from_server(self):
        server = self._create_stdio_server()
        server.start_server()
        echo = self.LLMTool.search(
            [("name", "=", "llm_mcp_test_echo"), ("mcp_server_id", "=", server.id)],
            limit=1,
        )
        echo.input_schema = json.dumps({"type": "object", "properties": {}})
        echo.action_reset_input_schema()
        schema = json.loads(echo.input_schema)
        self.assertIn("message", schema.get("properties", {}))
