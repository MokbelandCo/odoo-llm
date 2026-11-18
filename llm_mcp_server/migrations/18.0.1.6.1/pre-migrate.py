"""Mark the default MCP server XML ID noupdate before data reload.

``data/llm_mcp_server_config.xml`` ships ``tool_mode=all`` and an empty
external URL. Databases installed before 18.0.1.6.1 have
``llm_mcp_server.default_mcp_server_config`` with ``noupdate`` false, so a
module update rewrites administrator settings.

This script only sets that XML ID's ``noupdate`` flag. It does not update
``llm_mcp_server_config`` and it does not infer Selected Tools from a
nonempty tool list. An installation already reset to All Tools stays that
way until an administrator restores the intended settings.
"""

import logging

_logger = logging.getLogger(__name__)

_XML_MODULE = "llm_mcp_server"
_XML_NAME = "default_mcp_server_config"


def migrate(cr, version):
    cr.execute(
        """
        UPDATE ir_model_data
           SET noupdate = TRUE
         WHERE module = %s
           AND name = %s
           AND model = 'llm.mcp.server.config'
           AND noupdate IS NOT TRUE
        """,
        (_XML_MODULE, _XML_NAME),
    )
    if cr.rowcount:
        _logger.info(
            "Preserved MCP server settings on upgrade from %s: %s.%s is noupdate",
            version,
            _XML_MODULE,
            _XML_NAME,
        )
