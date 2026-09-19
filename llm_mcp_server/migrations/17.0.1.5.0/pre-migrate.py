"""Prepare 19.0.1.4.x multi-endpoint columns before ORM schema init.

``server_config_id`` is a new required Many2one whose field default used to
call ``get_active_config()``. Odoo evaluates that default in ``_init_column``
whenever ``llm_mcp_session`` already has rows, which happens during
``-u llm_mcp_server`` *before* XML data is loaded. If no active config serves
``/mcp`` yet (or ``endpoint_path`` is still empty), that raise aborts the
upgrade.

This script:

* Assigns ``/mcp`` to the oldest config and unique ``/mcp/config-<id>`` paths
  to the rest, so the new unique constraint can be applied.
* Backfills sessions onto a config when one exists.
* Drops leftover sessions when no config exists, so the new NOT NULL column
  can be added even with zero MCP servers.
"""


def migrate(cr, version):
    cr.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'llm_mcp_server_config'
          AND column_name = 'endpoint_path'
        """
    )
    if not cr.fetchone():
        cr.execute(
            "ALTER TABLE llm_mcp_server_config ADD COLUMN endpoint_path VARCHAR"
        )

    cr.execute(
        """
        SELECT 1 FROM llm_mcp_server_config
        WHERE endpoint_path = '/mcp'
        LIMIT 1
        """
    )
    has_legacy = bool(cr.fetchone())

    cr.execute(
        """
        SELECT id FROM llm_mcp_server_config
        WHERE endpoint_path IS NULL OR btrim(endpoint_path) = ''
        ORDER BY id
        """
    )
    for (config_id,) in cr.fetchall():
        if not has_legacy:
            path = "/mcp"
            has_legacy = True
        else:
            path = f"/mcp/config-{config_id}"
        cr.execute(
            "UPDATE llm_mcp_server_config SET endpoint_path = %s WHERE id = %s",
            (path, config_id),
        )

    cr.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'llm_mcp_session'
          AND column_name = 'server_config_id'
        """
    )
    if not cr.fetchone():
        cr.execute(
            "ALTER TABLE llm_mcp_session ADD COLUMN server_config_id INTEGER"
        )

    cr.execute(
        """
        UPDATE llm_mcp_session
        SET server_config_id = (
            SELECT id FROM llm_mcp_server_config
            WHERE endpoint_path = '/mcp'
            ORDER BY id
            LIMIT 1
        )
        WHERE server_config_id IS NULL
        """
    )
    cr.execute(
        """
        UPDATE llm_mcp_session
        SET server_config_id = (
            SELECT id FROM llm_mcp_server_config
            ORDER BY id
            LIMIT 1
        )
        WHERE server_config_id IS NULL
        """
    )
    cr.execute("DELETE FROM llm_mcp_session WHERE server_config_id IS NULL")
