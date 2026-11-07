"""Preserve public MCP initialization for configurations created before 1.6.

The ORM field defaults new records to the recommended protected-endpoint
policy. Adding and backfilling the column before registry initialization lets
existing deployments retain their prior operations-only behavior.
"""


def migrate(cr, version):
    cr.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'llm_mcp_server_config'
          AND column_name = 'authentication_policy'
        """
    )
    if not cr.fetchone():
        cr.execute(
            """
            ALTER TABLE llm_mcp_server_config
            ADD COLUMN authentication_policy VARCHAR
            """
        )

    cr.execute(
        """
        UPDATE llm_mcp_server_config
        SET authentication_policy = 'operations'
        WHERE authentication_policy IS NULL
        """
    )
