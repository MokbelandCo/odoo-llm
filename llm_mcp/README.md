# LLM MCP Client

Connect in-Odoo AI assistants to **external** MCP servers and import their tools into `llm.tool`.

**Module Type:** Infrastructure (MCP Client)

This is the inverse of `llm_mcp_server`:

| Module                  | Role                                                                                                          |
| ----------------------- | ------------------------------------------------------------------------------------------------------------- |
| `llm_mcp` (this module) | Odoo is an MCP **client**. It starts an external stdio server and registers that server's tools for LLM chat. |
| `llm_mcp_server`        | Odoo is an MCP **server**. Claude Desktop / Cursor connect _to_ Odoo.                                         |

Upstream Apexive dropped `llm_mcp` from 18.0 as not-ready. This 17.0 module is a backport of the 16.0 client, adapted to the 17.0 `llm.tool` execute/schema APIs.

## Installation

```bash
odoo-bin -d your_db -i llm_mcp
```

Dependencies: `llm`, `llm_tool`.

## Configure a stdio MCP server

1. Open **LLM → Configuration → MCP Clients**
2. Create a server with transport **Standard IO**
3. Set **Command** (executable) and **Arguments** (for example a filesystem MCP server)
4. Click **Start Server** to initialize the protocol and import tools
5. Use **Refresh Tools** after the remote tool list changes

Imported tools appear on `llm.tool` with implementation **MCP Client** and can be attached to assistants like any other tool.

## Configure an HTTP MCP server (OAuth)

1. Open **LLM → Configuration → MCP Clients**
2. Create a server with transport **Streamable HTTP** and the remote `/mcp` URL
3. Choose authentication:
   - **None** for open servers
   - **Static Bearer Token** for API keys
   - **OAuth 2.1** for RFC 9728 discovery
4. For OAuth client credentials, set client ID and secret, then **Start Server**
5. For authorization code + PKCE, set the client ID and click **Authorize**

The client sends RFC 8707 `resource` on token requests and retries once after a `401` with `WWW-Authenticate: resource_metadata=...`.

## Odoo 17 notes

- Views use Odoo 17 modifiers (`invisible=`, `<tree>`, `oe_chatter`) rather than 16.0 `attrs`.
- `llm.tool.execute()` validates arguments from the Python method signature. MCP tools skip that path and send JSON arguments to the remote server.
- Client protocol version is `2025-03-26` (16.0 sent `0.1.0`, which current MCP SDKs reject).
- Child processes are stopped on **Stop Server** and on unlink.
