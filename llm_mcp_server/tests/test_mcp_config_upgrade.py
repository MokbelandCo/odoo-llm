import importlib.util
from pathlib import Path

from odoo.tests import TransactionCase, tagged
from odoo.tools.convert import convert_file

_MIGRATION = (
    Path(__file__).resolve().parents[1] / "migrations" / "19.0.1.6.1" / "pre-migrate.py"
)
_CONFIG_FIELDS = (
    "name",
    "version",
    "latest_protocol_version",
    "active",
    "endpoint_path",
    "mode",
    "tool_mode",
    "oauth_enabled",
    "allow_api_key",
    "external_url",
    "authentication_policy",
    "oauth_issuer_url",
    "client_name",
)


def _run_pre_migrate(cr, version):
    spec = importlib.util.spec_from_file_location(
        "llm_mcp_server_pre_migrate_19_0_1_6_1", _MIGRATION
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.migrate(cr, version)


def _reload_default_config_xml(env, mode):
    convert_file(
        env,
        "llm_mcp_server",
        "data/llm_mcp_server_config.xml",
        {},
        mode=mode,
    )


@tagged("post_install", "-at_install")
class TestMcpDefaultConfigUpgrade(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Config = cls.env["llm.mcp.server.config"]
        cls.Tool = cls.env["llm.tool"]
        cls.config = cls.Config.get_active_config()
        cls.tool_a = cls.Tool.create(
            {
                "name": "mcp_upgrade_tool_a",
                "description": "Allowlisted upgrade tool",
                "implementation": "function",
                "input_schema": '{"type": "object", "properties": {}}',
                "decorator_model": "llm.mcp.server.config",
                "decorator_method": "mcp_upgrade_tool_a",
            }
        )
        cls.tool_b = cls.Tool.create(
            {
                "name": "mcp_upgrade_tool_b",
                "description": "Named endpoint tool",
                "implementation": "function",
                "input_schema": '{"type": "object", "properties": {}}',
                "decorator_model": "llm.mcp.server.config",
                "decorator_method": "mcp_upgrade_tool_b",
            }
        )

    def _default_xmlid(self):
        return self.env["ir.model.data"].search(
            [
                ("module", "=", "llm_mcp_server"),
                ("name", "=", "default_mcp_server_config"),
            ]
        )

    def _config_snapshot(self, config):
        self.env.flush_all()
        self.env.cr.execute(
            """
            SELECT tool_id
              FROM llm_mcp_server_config_tool_rel
             WHERE config_id = %s
             ORDER BY tool_id
            """,
            (config.id,),
        )
        tool_ids = [row[0] for row in self.env.cr.fetchall()]
        snapshot = {field: config[field] for field in _CONFIG_FIELDS}
        snapshot["tool_ids"] = tool_ids
        return snapshot

    def _upgrade_default_config(self):
        self.env.flush_all()
        _run_pre_migrate(self.env.cr, "19.0.1.6.0")
        self.env.invalidate_all()
        _reload_default_config_xml(self.env, mode="update")
        self.env.invalidate_all()

    def test_upgrade_preserves_selected_tools_and_marks_noupdate(self):
        """Upgrade from noupdate=false keeps the default endpoint and sets the flag."""
        named = self.Config.create(
            {
                "name": "named_all_tools",
                "version": "3.0.0",
                "latest_protocol_version": "2025-11-25",
                "active": True,
                "endpoint_path": "/mcp/named",
                "mode": "stateful",
                "tool_mode": "all",
                "tool_ids": [(6, 0, [self.tool_b.id])],
                "external_url": "https://named.example",
                "authentication_policy": "protected",
                "oauth_enabled": True,
                "allow_api_key": False,
                "oauth_issuer_url": "https://auth.named.example",
                "client_name": "named-client",
            }
        )
        imd = self._default_xmlid()
        imd.noupdate = False
        self.config.write(
            {
                "name": "restricted_mcp",
                "version": "2.4.1",
                "latest_protocol_version": "2025-06-18",
                "active": True,
                "endpoint_path": "/mcp",
                "mode": "stateless",
                "tool_mode": "selected",
                "tool_ids": [(6, 0, [self.tool_a.id])],
                "external_url": "https://odoo.example",
                "authentication_policy": "operations",
                "oauth_enabled": False,
                "allow_api_key": True,
                "oauth_issuer_url": "https://auth.example",
                "client_name": "restricted-client",
            }
        )
        before = self._config_snapshot(self.config)
        named_before = self._config_snapshot(named)

        self._upgrade_default_config()

        self.assertEqual(self._config_snapshot(self.config), before)
        self.assertEqual(self._config_snapshot(named), named_before)
        self.assertTrue(self._default_xmlid().noupdate)
        self.assertEqual(self.config.tool_mode, "selected")
        self.assertEqual(self.config.tool_ids, self.tool_a)

        self._upgrade_default_config()

        self.assertEqual(self._config_snapshot(self.config), before)
        self.assertEqual(self._config_snapshot(named), named_before)
        self.assertTrue(self._default_xmlid().noupdate)

    def test_upgrade_keeps_explicit_all_tools_with_a_nonempty_list(self):
        """A nonempty tool list stays All Tools when that mode was chosen."""
        imd = self._default_xmlid()
        imd.noupdate = False
        self.config.write(
            {
                "tool_mode": "all",
                "tool_ids": [(6, 0, [self.tool_a.id, self.tool_b.id])],
                "external_url": "https://all-tools.example",
                "endpoint_path": "/mcp",
                "authentication_policy": "protected",
                "oauth_enabled": True,
                "allow_api_key": True,
            }
        )
        before = self._config_snapshot(self.config)

        self._upgrade_default_config()

        self.assertEqual(self._config_snapshot(self.config), before)
        self.assertEqual(self.config.tool_mode, "all")
        self.assertEqual(set(self.config.tool_ids.ids), {self.tool_a.id, self.tool_b.id})
        self.assertTrue(self._default_xmlid().noupdate)

    def test_database_noupdate_blocks_a_reset_payload(self):
        """The stored flag blocks XML values even when the file is updatable.

        ``noupdate="1"`` on a fresh copy of the data file does not update
        ``ir.model.data`` for a record that already exists. The pre-migrate
        flag is what stops a reload of the old updatable record.
        """
        imd = self._default_xmlid()
        imd.noupdate = False
        self.config.write(
            {
                "tool_mode": "selected",
                "tool_ids": [(6, 0, [self.tool_a.id])],
                "external_url": "https://keep.example",
                "endpoint_path": "/mcp",
                "authentication_policy": "operations",
                "oauth_enabled": False,
                "allow_api_key": True,
            }
        )
        self.env.flush_all()
        reset_values = {
            "tool_mode": "all",
            "external_url": False,
            "mode": "stateful",
            "oauth_enabled": True,
            "authentication_policy": "protected",
        }
        self.Config._load_records(
            [
                {
                    "xml_id": "llm_mcp_server.default_mcp_server_config",
                    "noupdate": False,
                    "values": reset_values,
                }
            ],
            update=True,
        )
        self.assertEqual(self.config.tool_mode, "all")
        self.assertFalse(self.config.external_url)

        self.config.write(
            {
                "tool_mode": "selected",
                "tool_ids": [(6, 0, [self.tool_a.id])],
                "external_url": "https://keep.example",
                "endpoint_path": "/mcp",
                "mode": "stateless",
                "authentication_policy": "operations",
                "oauth_enabled": False,
                "allow_api_key": True,
            }
        )
        imd.noupdate = False
        before = self._config_snapshot(self.config)

        self.env.flush_all()
        _run_pre_migrate(self.env.cr, "19.0.1.6.0")
        self.env.invalidate_all()
        self.assertTrue(self._default_xmlid().noupdate)

        self.Config._load_records(
            [
                {
                    "xml_id": "llm_mcp_server.default_mcp_server_config",
                    "noupdate": False,
                    "values": reset_values,
                }
            ],
            update=True,
        )
        self.env.invalidate_all()

        self.assertEqual(self._config_snapshot(self.config), before)
        self.assertTrue(self._default_xmlid().noupdate)

    def test_pre_migrate_is_idempotent_without_the_xmlid(self):
        self._default_xmlid().unlink()
        self.env.flush_all()
        _run_pre_migrate(self.env.cr, "19.0.1.6.0")
        _run_pre_migrate(self.env.cr, "19.0.1.6.1")

    def test_init_creates_the_default_config_as_noupdate(self):
        config = self.config
        imd = self._default_xmlid()
        self.env["llm.mcp.session"].search(
            [("server_config_id", "=", config.id)]
        ).unlink()
        config.unlink()
        imd.unlink()

        _reload_default_config_xml(self.env, mode="init")
        self.env.invalidate_all()

        created = self.env.ref("llm_mcp_server.default_mcp_server_config")
        self.assertTrue(self._default_xmlid().noupdate)
        self.assertEqual(created.tool_mode, "all")
        self.assertEqual(created.endpoint_path, "/mcp")
        self.assertFalse(created.external_url)
        self.assertEqual(created.mode, "stateful")
        self.assertTrue(created.oauth_enabled)
        self.assertTrue(created.allow_api_key)
