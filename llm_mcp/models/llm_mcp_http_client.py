import json
import threading
from urllib.parse import urlparse

import requests
from requests.exceptions import RequestException

from odoo.exceptions import UserError

from .llm_mcp_bus_manager import (
    MCP_CLIENT_INFO,
    MCP_CLIENT_PROTOCOL_VERSION,
)

DEFAULT_TIMEOUT = 30


def _parse_www_authenticate_resource_metadata(header):
    if not header:
        return None
    for part in header.split(","):
        part = part.strip()
        if part.lower().startswith("resource_metadata="):
            return part.split("=", 1)[1].strip().strip('"')
    return None


def _parse_json_or_sse(response):
    if response.status_code in (202, 204) or not (response.text or "").strip():
        return {}
    content_type = (response.headers.get("Content-Type") or "").lower()
    text = response.text or ""
    if "text/event-stream" in content_type:
        data_lines = []
        for line in text.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if not data_lines:
            raise UserError("Empty SSE response from MCP server")
        return json.loads(data_lines[-1])
    if not text:
        return {}
    return response.json()


class MCPHttpClient:
    """Streamable HTTP MCP client with optional OAuth 2.1."""

    _instances = {}
    _lock = threading.Lock()

    def __new__(cls, env, server):
        key = f"http_{server.id}"
        with cls._lock:
            instance = cls._instances.get(key)
            if instance is None:
                instance = super().__new__(cls)
                instance._init_properties(env, server)
                cls._instances[key] = instance
            else:
                instance.env = env
                instance.server_id = server.id
            return instance

    @classmethod
    def discard(cls, server_id):
        with cls._lock:
            cls._instances.pop(f"http_{server_id}", None)

    def _init_properties(self, env, server):
        self.env = env
        self.server_id = server.id
        self._initialized = False
        self._request_counter = 0
        self.protocol_version = None
        self.server_info = None
        self.session_id = None

    def _record(self):
        return self.env["llm.mcp.server"].browse(self.server_id)

    def _next_id(self):
        with self._lock:
            self._request_counter += 1
            return self._request_counter

    def _headers(self, server, access_token=None):
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Mcp-Protocol-Version": MCP_CLIENT_PROTOCOL_VERSION,
        }
        token = access_token or server.get_http_access_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def _post(self, server, payload, allow_oauth_retry=True):
        url = (server.url or "").strip()
        if not url:
            raise UserError("HTTP MCP server URL is required")
        try:
            response = requests.post(
                url,
                json=payload,
                headers=self._headers(server),
                timeout=DEFAULT_TIMEOUT,
            )
        except RequestException as exc:
            raise UserError(f"MCP HTTP request failed: {exc}") from exc

        if (
            response.status_code == 401
            and allow_oauth_retry
            and server.auth_type == "oauth"
        ):
            metadata_url = _parse_www_authenticate_resource_metadata(
                response.headers.get("WWW-Authenticate")
            )
            server._refresh_oauth_token(metadata_url=metadata_url)
            server.invalidate_recordset()
            return self._post(server, payload, allow_oauth_retry=False)

        if response.status_code >= 400:
            raise UserError(
                f"MCP HTTP {response.status_code}: {(response.text or '')[:500]}"
            )

        session_id = response.headers.get("Mcp-Session-Id")
        if session_id:
            self.session_id = session_id
        return _parse_json_or_sse(response)

    def initialize(self):
        server = self._record()
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "clientInfo": MCP_CLIENT_INFO,
                "protocolVersion": MCP_CLIENT_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
            },
        }
        body = self._post(server, payload)
        result = body.get("result") or {}
        self.protocol_version = result.get("protocolVersion")
        self.server_info = result.get("serverInfo")
        self._initialized = True
        self._post(
            server,
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
        )
        return True

    def list_tools(self):
        if not self._initialized:
            self.initialize()
        server = self._record()
        body = self._post(
            server,
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/list",
                "params": {},
            },
        )
        result = body.get("result") or {}
        if "error" in body:
            raise UserError(body["error"].get("message") or "tools/list failed")
        return result.get("tools") or []

    def call_tool(self, tool_name, arguments):
        if not self._initialized:
            self.initialize()
        server = self._record()
        body = self._post(
            server,
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments or {}},
            },
        )
        if "error" in body:
            return {"error": body["error"].get("message") or "tools/call failed"}
        result = body.get("result") or {}
        if result.get("isError"):
            error_content = ""
            for content_item in result.get("content") or []:
                if content_item.get("type") == "text":
                    error_content += content_item.get("text", "")
            return {"error": error_content or "Tool execution failed"}
        content_result = {}
        for content_item in result.get("content") or []:
            if content_item.get("type") == "text":
                text_content = content_item.get("text", "")
                try:
                    content_result = json.loads(text_content)
                except json.JSONDecodeError:
                    content_result = {"result": text_content}
        return content_result


def discover_protected_resource_metadata(resource_url, metadata_url=None):
    urls = []
    if metadata_url:
        urls.append(metadata_url)
    parsed = urlparse(resource_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")
    if path:
        urls.append(f"{origin}/.well-known/oauth-protected-resource{path}")
    urls.append(f"{origin}/.well-known/oauth-protected-resource")
    last_error = None
    for url in urls:
        try:
            response = requests.get(url, timeout=DEFAULT_TIMEOUT)
            if response.status_code == 200:
                return response.json()
        except RequestException as exc:
            last_error = exc
    raise UserError(
        f"Could not fetch OAuth protected resource metadata for {resource_url}: {last_error}"
    )


def discover_authorization_server_metadata(issuer):
    issuer = issuer.rstrip("/")
    parsed = urlparse(issuer)
    path = parsed.path.rstrip("/")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    urls = []
    if path:
        urls.append(f"{origin}/.well-known/oauth-authorization-server{path}")
    urls.append(f"{issuer}/.well-known/oauth-authorization-server")
    last_error = None
    for url in urls:
        try:
            response = requests.get(url, timeout=DEFAULT_TIMEOUT)
            if response.status_code == 200:
                return response.json()
        except RequestException as exc:
            last_error = exc
    raise UserError(f"Could not fetch authorization server metadata: {last_error}")


def request_oauth_token(token_endpoint, data, client_id=None, client_secret=None):
    auth = None
    if client_id and client_secret:
        auth = (client_id, client_secret)
    try:
        response = requests.post(
            token_endpoint,
            data=data,
            auth=auth,
            headers={"Accept": "application/json"},
            timeout=DEFAULT_TIMEOUT,
        )
    except RequestException as exc:
        raise UserError(f"OAuth token request failed: {exc}") from exc
    if response.status_code >= 400:
        raise UserError(
            f"OAuth token error {response.status_code}: {(response.text or '')[:500]}"
        )
    return response.json()
