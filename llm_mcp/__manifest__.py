{
    "name": "LLM MCP Client",
    "version": "19.0.1.1.0",
    "category": "Technical",
    "summary": "MCP client that imports tools from external Model Context Protocol servers",
    "description": """
        Model Context Protocol (MCP) Client for Odoo LLM

        Connect Odoo AI chat to external MCP-compliant servers that provide tool
        implementations. This is the inverse of llm_mcp_server (which exposes Odoo
        tools to Claude Desktop and other MCP hosts).

        Core Features:
        • Connect to external MCP servers over stdio or streamable HTTP
        • OAuth 2.1 (RFC 9728 discovery, PKCE, client credentials) or static Bearer tokens
        • Auto-discover and register tools exposed by those servers
        • Execute MCP tools from in-Odoo LLM conversations
        • Management UI for MCP server connections under LLM Configuration
    """,
    "author": "Apexive Solutions LLC",
    "website": "https://github.com/apexive/odoo-llm",
    "license": "LGPL-3",
    "depends": ["base", "mail", "llm", "llm_tool"],
    "external_dependencies": {
        "python": ["requests"],
    },
    "data": [
        "security/ir.model.access.csv",
        "views/llm_mcp_server_views.xml",
        "views/llm_tool_views.xml",
        "views/llm_menu_views.xml",
    ],
    "images": [
        "static/description/banner.jpeg",
        "static/description/mcp-diagram.png",
    ],
    "auto_install": False,
    "application": False,
    "installable": True,
}
