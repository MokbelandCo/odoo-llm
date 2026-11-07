import json

from odoo import api, fields, models
from odoo.exceptions import UserError


class LLMTool(models.Model):
    _inherit = "llm.tool"

    mcp_server_id = fields.Many2one(
        "llm.mcp.server",
        string="MCP Server",
        ondelete="cascade",
        help="External MCP server that provides this tool when implementation is MCP Client.",
    )

    @api.model
    def _get_available_implementations(self):
        implementations = super()._get_available_implementations()
        return implementations + [("mcp", "MCP Client")]

    def get_input_schema(self):
        """MCP tools always use the schema advertised by the remote server.

        Do not generate a schema from mcp_execute()'s ``**parameters`` signature.
        """
        self.ensure_one()
        if self.implementation == "mcp":
            if self.input_schema:
                return json.loads(self.input_schema)
            return {"type": "object", "properties": {}}
        return super().get_input_schema()

    def mcp_execute(self, **parameters):
        """Execute the tool on the remote MCP server."""
        self.ensure_one()

        if not self.mcp_server_id:
            raise UserError("This tool is not associated with an MCP server")

        if not self.mcp_server_id.is_active:
            raise UserError(f"MCP server '{self.mcp_server_id.name}' is not active")

        try:
            result = self.mcp_server_id.execute_tool(self.name, parameters)

            if result and isinstance(result, dict) and "error" in result:
                error_message = result["error"]
                raise UserError(f"Tool execution failed: {error_message}")

            return result
        except Exception as e:
            if not isinstance(e, UserError):
                raise UserError(
                    f"Error executing tool '{self.name}' on MCP server "
                    f"'{self.mcp_server_id.name}': {str(e)}"
                ) from e
            raise

    def execute(self, parameters):
        """Skip pydantic-from-signature validation for MCP tools.

        llm.tool.execute() builds a Pydantic model from the implementation
        method signature. mcp_execute(self, **parameters) cannot describe a
        remote tool's dynamic JSON schema, so MCP calls pass arguments through
        unchanged.
        """
        self.ensure_one()
        if self.implementation == "mcp":
            return self.mcp_execute(**(parameters or {}))
        return super().execute(parameters)

    def action_reset_input_schema(self):
        mcp_tools = self.filtered(lambda tool: tool.implementation == "mcp")
        other_tools = self - mcp_tools
        for tool in mcp_tools:
            if tool.mcp_server_id:
                tool.mcp_server_id.list_tools()
        if other_tools:
            return super(LLMTool, other_tools).action_reset_input_schema()
        return {
            "type": "ir.actions.client",
            "tag": "reload",
        }
