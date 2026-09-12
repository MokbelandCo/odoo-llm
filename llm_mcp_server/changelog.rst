19.0.1.3.0 (2026-09-12)
~~~~~~~~~~~~~~~~~~~~~~~

* [ADD] Per-config MCP tool exposure: all active tools, or a selected subset
* [IMP] ``tools/list`` and ``tools/call`` honor the active config allowlist

19.0.1.2.1 (2026-09-10)
~~~~~~~~~~~~~~~~~~~~~~~

* [FIX] OAuth authorize/error pages no longer crash with ``KeyError: 'website'`` when Website is installed
* [FIX] Authorization redirects now build the callback URL (``urlunparse`` was not imported)
* [ADD] HTTP tests for the login-gated consent page, allow/deny, and PKCE token exchange

19.0.1.2.0 (2026-09-09)
~~~~~~~~~~~~~~~~~~~~~~~

* [ADD] OAuth 2.1 for the MCP HTTP resource server (RFC 9728 / 8414 / 7591 / 8707)
* [ADD] Protected resource and authorization server well-known metadata
* [ADD] Authorization code + PKCE, refresh tokens, client credentials, and DCR
* [IMP] ``WWW-Authenticate`` on 401 now advertises ``resource_metadata``
* [IMP] Bearer auth accepts OAuth access tokens or existing Odoo API keys

18.0.1.3.1 (2026-01-07)
~~~~~~~~~~~~~~~~~~~~~~~

* [FIX] Fixed session deletion permission - all authenticated users can now delete sessions
* [IMP] Simplified permission model for MCP session management

18.0.1.3.0 (2025-12-02)
~~~~~~~~~~~~~~~~~~~~~~~

* [ADD] New MCP Key wizard - generates API key with ready-to-copy client configurations
* [ADD] "New MCP Key" button in user preferences (Account Security section)
* [ADD] "New MCP Key" button in MCP Server Config form for quick key generation
* [IMP] Client configs now use Jinja2 templates for maintainable config generation
* [IMP] Nested notebook tabs for client configurations with CopyClipboardButton widgets
* [IMP] DRY refactoring - shared config generation between wizard and config form

18.0.1.2.0 (2025-11-28)
~~~~~~~~~~~~~~~~~~~~~~~

* [ADD] Added "Client Configuration" tab to MCP Server Config form with copy-paste setup instructions
* [ADD] Included configuration snippets for Claude Desktop, Claude Code, and Codex CLI
* [ADD] Added prerequisites section with mcp-remote installation command
* [IMP] Better onboarding experience with inline API key guidance

18.0.1.1.0 (2025-11-03)
~~~~~~~~~~~~~~~~~~~~~~~

* [IMP] Updated Odoo App Store description page (static/description/index.html)
* [IMP] Improved module presentation with modern Bootstrap 5 layout
* [IMP] Enhanced mobile responsiveness and visual design
* [IMP] Added comprehensive MCP feature descriptions and use cases
* [IMP] Optimized for Odoo App Store HTML sanitization requirements

18.0.1.0.0 (2025-10-23)
~~~~~~~~~~~~~~~~~~~~~~~

* [INIT] Initial release of the module
* [ADD] MCP 2025-06-18 protocol implementation
* [ADD] Bearer token authentication with Odoo API keys
* [ADD] Dynamic tool discovery from llm.tool registry
* [ADD] Real-time tool execution with proper Odoo context
* [ADD] Health monitoring and session management
* [ADD] Support for Claude Desktop, Claude Code, Cursor, Windsurf, VS Code, and Codex
