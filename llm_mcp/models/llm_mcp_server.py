import base64
import json
import logging
import secrets
from datetime import timedelta
from hashlib import sha256
from urllib.parse import urlencode, urlparse

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .llm_mcp_bus_manager import MCPBusManager
from .llm_mcp_http_client import (
    MCPHttpClient,
    discover_authorization_server_metadata,
    discover_protected_resource_metadata,
    request_oauth_token,
)

_logger = logging.getLogger(__name__)


class LLMMCPServer(models.Model):
    _name = "llm.mcp.server"
    _description = "LLM MCP Server (Client Connection)"
    _inherit = ["mail.thread"]

    name = fields.Char(required=True, tracking=True)
    transport = fields.Selection(
        [
            ("internal", "Internal"),
            ("stdio", "Standard IO"),
            ("http", "Streamable HTTP"),
        ],
        string="Transport Type",
        default="stdio",
        required=True,
        tracking=True,
    )

    command = fields.Char(
        string="Command",
        help="Command to execute when transport is stdio",
        tracking=True,
    )
    args = fields.Char(
        string="Arguments",
        help="Command line arguments for the command",
        tracking=True,
    )
    url = fields.Char(
        string="Server URL",
        help="Streamable HTTP MCP endpoint (for example https://example.com/mcp)",
        tracking=True,
    )
    auth_type = fields.Selection(
        [
            ("none", "None"),
            ("bearer", "Static Bearer Token"),
            ("oauth", "OAuth 2.1"),
        ],
        string="Authentication",
        default="none",
        required=True,
        tracking=True,
    )
    bearer_token = fields.Char(string="Bearer Token")
    oauth_grant_type = fields.Selection(
        [
            ("client_credentials", "Client Credentials"),
            ("authorization_code", "Authorization Code + PKCE"),
        ],
        default="client_credentials",
    )
    oauth_client_id = fields.Char()
    oauth_client_secret = fields.Char()
    oauth_scope = fields.Char(default="mcp:tools")
    oauth_access_token = fields.Char()
    oauth_refresh_token = fields.Char()
    oauth_token_expiry = fields.Datetime()
    oauth_code_verifier = fields.Char()
    oauth_state = fields.Char()
    oauth_issuer = fields.Char(readonly=True)
    oauth_authorization_endpoint = fields.Char(readonly=True)
    oauth_token_endpoint = fields.Char(readonly=True)

    tool_ids = fields.One2many("llm.tool", "mcp_server_id", string="MCP Tools")

    is_connected = fields.Boolean(string="Connected", default=False, tracking=True)
    is_active = fields.Boolean(string="Active", default=True, tracking=True)

    protocol_version = fields.Char(string="Protocol Version", readonly=True)
    server_info = fields.Char(string="Server Info", readonly=True)

    @api.constrains("transport", "command", "url")
    def _check_command(self):
        for server in self:
            if server.transport == "stdio" and not server.command:
                raise ValidationError(
                    _("Command is required for Standard IO transport")
                )
            if server.transport == "http" and not server.url:
                raise ValidationError(_("Server URL is required for HTTP transport"))

    def _get_manager(self):
        """Get a manager instance for this server"""
        if self.transport != "stdio":
            return None

        try:
            return MCPBusManager(self.env, self.id, self.command, self.args)
        except Exception as e:
            error_msg = f"Failed to get manager for server {self.name}: {str(e)}"
            _logger.error(error_msg)
            raise UserError(error_msg) from e

    def _get_http_client(self):
        self.ensure_one()
        return MCPHttpClient(self.env, self)

    def get_http_access_token(self):
        self.ensure_one()
        if self.auth_type == "bearer":
            return self.bearer_token
        if self.auth_type == "oauth":
            if self.oauth_access_token and (
                not self.oauth_token_expiry
                or self.oauth_token_expiry > fields.Datetime.now()
            ):
                return self.oauth_access_token
            self._refresh_oauth_token()
            return self.oauth_access_token
        return None

    def _canonical_resource(self):
        self.ensure_one()
        url = (self.url or "").strip().rstrip("/")
        parsed = urlparse(url)
        if not parsed.scheme:
            return url
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"

    def _store_token_response(self, payload):
        self.ensure_one()
        expires = False
        if payload.get("expires_in"):
            expires = fields.Datetime.now() + timedelta(
                seconds=int(payload["expires_in"])
            )
        self.write(
            {
                "oauth_access_token": payload.get("access_token"),
                "oauth_refresh_token": payload.get("refresh_token")
                or self.oauth_refresh_token,
                "oauth_token_expiry": expires,
            }
        )

    def _discover_oauth_endpoints(self, metadata_url=None):
        self.ensure_one()
        prm = discover_protected_resource_metadata(
            self._canonical_resource(), metadata_url=metadata_url
        )
        issuers = prm.get("authorization_servers") or []
        if not issuers:
            raise UserError(
                _("Protected resource metadata did not list an authorization server.")
            )
        issuer = issuers[0]
        as_meta = discover_authorization_server_metadata(issuer)
        self.write(
            {
                "oauth_issuer": issuer,
                "oauth_authorization_endpoint": as_meta.get("authorization_endpoint"),
                "oauth_token_endpoint": as_meta.get("token_endpoint"),
            }
        )
        return as_meta

    def _refresh_oauth_token(self, metadata_url=None):
        self.ensure_one()
        if self.auth_type != "oauth":
            return
        if not self.oauth_token_endpoint:
            self._discover_oauth_endpoints(metadata_url=metadata_url)
        resource = self._canonical_resource()
        if self.oauth_refresh_token:
            payload = request_oauth_token(
                self.oauth_token_endpoint,
                {
                    "grant_type": "refresh_token",
                    "refresh_token": self.oauth_refresh_token,
                    "resource": resource,
                    "client_id": self.oauth_client_id or "",
                },
                client_id=self.oauth_client_id,
                client_secret=self.oauth_client_secret,
            )
            self._store_token_response(payload)
            return
        if self.oauth_grant_type == "client_credentials":
            if not self.oauth_client_id or not self.oauth_client_secret:
                raise UserError(
                    _("OAuth client ID and secret are required for client credentials.")
                )
            payload = request_oauth_token(
                self.oauth_token_endpoint,
                {
                    "grant_type": "client_credentials",
                    "resource": resource,
                    "scope": self.oauth_scope or "mcp:tools",
                    "client_id": self.oauth_client_id,
                    "client_secret": self.oauth_client_secret,
                },
                client_id=self.oauth_client_id,
                client_secret=self.oauth_client_secret,
            )
            self._store_token_response(payload)
            return
        raise UserError(
            _(
                "No OAuth token is stored. Use Authorize to complete the authorization code flow."
            )
        )

    def action_oauth_authorize(self):
        """Start the OAuth authorization code + PKCE flow in the browser."""
        self.ensure_one()
        if self.transport != "http" or self.auth_type != "oauth":
            raise UserError(
                _("OAuth authorization is only available for HTTP OAuth clients.")
            )
        if self.oauth_grant_type != "authorization_code":
            raise UserError(_("Set the grant type to Authorization Code + PKCE first."))
        as_meta = self._discover_oauth_endpoints()
        if not as_meta.get("authorization_endpoint"):
            raise UserError(
                _("Authorization server did not advertise an authorization endpoint.")
            )
        verifier = secrets.token_urlsafe(64)
        digest = sha256(verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        state = secrets.token_urlsafe(16)
        base_url = (
            self.env["ir.config_parameter"].sudo().get_param("web.base.url").rstrip("/")
        )
        redirect_uri = f"{base_url}/mcp/client/oauth/callback"
        self.write({"oauth_code_verifier": verifier, "oauth_state": state})
        query = {
            "response_type": "code",
            "client_id": self.oauth_client_id or "",
            "redirect_uri": redirect_uri,
            "scope": self.oauth_scope or "mcp:tools",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": self._canonical_resource(),
        }
        url = as_meta["authorization_endpoint"]
        separator = "&" if urlparse(url).query else "?"
        return {
            "type": "ir.actions.act_url",
            "url": f"{url}{separator}{urlencode(query)}",
            "target": "self",
        }

    def complete_oauth_authorization(self, code):
        self.ensure_one()
        if not self.oauth_token_endpoint:
            self._discover_oauth_endpoints()
        base_url = (
            self.env["ir.config_parameter"].sudo().get_param("web.base.url").rstrip("/")
        )
        payload = request_oauth_token(
            self.oauth_token_endpoint,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": f"{base_url}/mcp/client/oauth/callback",
                "code_verifier": self.oauth_code_verifier,
                "resource": self._canonical_resource(),
                "client_id": self.oauth_client_id or "",
            },
            client_id=self.oauth_client_id,
            client_secret=self.oauth_client_secret,
        )
        self._store_token_response(payload)
        self.oauth_code_verifier = False
        self.oauth_state = False
        return True

    def start_server(self):
        """Start the server and connect to it"""
        self.ensure_one()
        if self.is_connected:
            return True

        if self.transport == "stdio":
            return self._start_stdio_server()
        if self.transport == "http":
            return self._start_http_server()
        if self.transport == "internal":
            self.is_connected = True
            return True

    def _start_stdio_server(self):
        try:
            manager = self._get_manager()
            if not manager:
                raise UserError(f"Failed to create manager for server {self.name}")
            if not manager._start_process():
                raise UserError(f"Failed to start process for server {self.name}")
            if not manager._initialized and not manager._initialize_mcp():
                raise UserError(
                    f"Failed to initialize MCP protocol for server {self.name}"
                )
            tools = manager.list_tools()
            if tools is None:
                raise UserError(f"Failed to retrieve tools from server {self.name}")

            self.is_connected = True
            self._update_tools(tools)
            if getattr(manager, "protocol_version", None):
                self.protocol_version = manager.protocol_version
            if getattr(manager, "server_info", None):
                self.server_info = json.dumps(manager.server_info or {})
            return True
        except Exception as e:
            MCPBusManager.discard(self.id)
            error_msg = f"Failed to start MCP server {self.name}: {str(e)}"
            _logger.error(error_msg)
            raise UserError(error_msg) from e

    def _start_http_server(self):
        try:
            if self.auth_type == "oauth" and not self.oauth_access_token:
                self._refresh_oauth_token()
            client = self._get_http_client()
            if not client.initialize():
                raise UserError(f"Failed to initialize MCP HTTP server {self.name}")
            tools = client.list_tools()
            self.is_connected = True
            self._update_tools(tools)
            if client.protocol_version:
                self.protocol_version = client.protocol_version
            if client.server_info:
                self.server_info = json.dumps(client.server_info or {})
            return True
        except Exception as e:
            MCPHttpClient.discard(self.id)
            error_msg = f"Failed to start MCP HTTP server {self.name}: {str(e)}"
            _logger.error(error_msg)
            raise UserError(error_msg) from e

    def stop_server(self):
        """Stop the server and disconnect from it"""
        self.ensure_one()
        if not self.is_connected and self.transport != "stdio":
            return True

        if self.transport == "stdio":
            try:
                MCPBusManager.discard(self.id)
            except Exception as e:
                error_msg = f"Error stopping MCP server {self.name}: {str(e)}"
                _logger.error(error_msg)
        elif self.transport == "http":
            MCPHttpClient.discard(self.id)

        self.is_connected = False
        return True

    def unlink(self):
        for server in self:
            try:
                MCPBusManager.discard(server.id)
                MCPHttpClient.discard(server.id)
            except Exception:
                _logger.exception(
                    "Failed to stop MCP process while unlinking server %s", server.id
                )
        return super().unlink()

    def list_tools(self):
        """Fetch and update tools from the MCP server"""
        self.ensure_one()

        if not self.is_connected:
            try:
                if not self.start_server():
                    raise UserError(f"Could not connect to MCP server {self.name}")
            except Exception as e:
                raise UserError(
                    f"Could not connect to MCP server {self.name}: {str(e)}"
                ) from e

        if self.transport == "stdio":
            try:
                manager = self._get_manager()
                if not manager:
                    raise UserError(f"Could not connect to MCP server {self.name}")

                tools = manager.list_tools()
                if tools is None:
                    raise UserError(
                        f"Failed to fetch tools from MCP server {self.name}"
                    )

                self._update_tools(tools)
                return self.tool_ids
            except Exception as e:
                error_msg = f"Error listing tools from server {self.name}: {str(e)}"
                _logger.error(error_msg)
                raise UserError(error_msg) from e
        elif self.transport == "http":
            try:
                client = self._get_http_client()
                tools = client.list_tools()
                self._update_tools(tools)
                return self.tool_ids
            except Exception as e:
                error_msg = (
                    f"Error listing tools from HTTP server {self.name}: {str(e)}"
                )
                _logger.error(error_msg)
                raise UserError(error_msg) from e
        elif self.transport == "internal":
            return self.tool_ids

    def _update_tools(self, tools_data):
        """Update or create tools based on the data from the MCP server"""
        Tool = self.env["llm.tool"]

        existing_tools = {tool.name: tool for tool in self.tool_ids}
        updated_tools = []

        for tool_data in tools_data:
            tool_name = tool_data.get("name")
            if not tool_name:
                continue

            tool = existing_tools.get(tool_name)

            input_schema = tool_data.get("inputSchema") or {}
            description = tool_data.get("description") or tool_name

            tool_values = {
                "name": tool_name,
                "description": description,
                "implementation": "mcp",
                "mcp_server_id": self.id,
                "input_schema": json.dumps(input_schema),
            }

            annotations = tool_data.get("annotations") or {}
            if "title" in annotations:
                tool_values["title"] = annotations["title"]
            if "readOnlyHint" in annotations:
                tool_values["read_only_hint"] = annotations["readOnlyHint"]
            if "idempotentHint" in annotations:
                tool_values["idempotent_hint"] = annotations["idempotentHint"]
            if "destructiveHint" in annotations:
                tool_values["destructive_hint"] = annotations["destructiveHint"]
            if "openWorldHint" in annotations:
                tool_values["open_world_hint"] = annotations["openWorldHint"]

            if tool:
                tool.write(tool_values)
                updated_tools.append(tool.id)
            else:
                tool = Tool.create(tool_values)
                updated_tools.append(tool.id)

        tools_to_delete = [
            tool
            for name, tool in existing_tools.items()
            if tool.id not in updated_tools
        ]
        if tools_to_delete:
            Tool.browse([t.id for t in tools_to_delete]).unlink()

        return True

    def execute_tool(self, tool_name, parameters):
        """Execute a tool on the MCP server"""
        self.ensure_one()

        if not self.is_connected:
            try:
                if not self.start_server():
                    raise UserError(f"Could not connect to MCP server {self.name}")
            except Exception as e:
                raise UserError(
                    f"Could not connect to MCP server {self.name}: {str(e)}"
                ) from e

        if self.transport == "stdio":
            try:
                manager = self._get_manager()
                if not manager:
                    raise UserError(f"Could not connect to MCP server {self.name}")

                result = manager.call_tool(tool_name, parameters)
                if result is None:
                    raise UserError(
                        f"Failed to execute tool {tool_name} on server {self.name}"
                    )
                return result
            except Exception as e:
                error_msg = f"Error executing tool {tool_name} on MCP server: {str(e)}"
                _logger.error(error_msg)
                return {"error": str(e)}
        elif self.transport == "http":
            try:
                client = self._get_http_client()
                result = client.call_tool(tool_name, parameters)
                if result is None:
                    raise UserError(
                        f"Failed to execute tool {tool_name} on server {self.name}"
                    )
                return result
            except Exception as e:
                error_msg = (
                    f"Error executing tool {tool_name} on MCP HTTP server: {str(e)}"
                )
                _logger.error(error_msg)
                return {"error": str(e)}
        elif self.transport == "internal":
            return {"error": "Execution not implemented for internal transport"}
