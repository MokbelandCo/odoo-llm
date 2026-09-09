from odoo import http
from odoo.http import request


class LLMMCPClientOAuthController(http.Controller):
    @http.route(
        "/mcp/client/oauth/callback",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def oauth_callback(self, **kwargs):
        error = kwargs.get("error")
        if error:
            return request.redirect(
                "/web#action=llm_mcp.action_llm_mcp_server"
            )
        state = kwargs.get("state")
        code = kwargs.get("code")
        if not state or not code:
            return request.not_found()
        server = request.env["llm.mcp.server"].search(
            [("oauth_state", "=", state)], limit=1
        )
        if not server:
            return request.not_found()
        server.complete_oauth_authorization(code)
        return request.redirect(
            f"/web#id={server.id}&model=llm.mcp.server&view_type=form"
        )
