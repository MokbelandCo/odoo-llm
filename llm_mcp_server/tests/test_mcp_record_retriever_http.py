import json
import uuid
from datetime import timedelta

from odoo import fields
from odoo.tests import HttpCase, tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestMcpRecordRetrieverHttp(HttpCase):
    """odoo_record_retriever over HTTP runs as the bearer user, without sudo."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.env["llm.mcp.server.config"].get_active_config()
        token = uuid.uuid4().hex
        cls.company = cls.env.company
        cls.other_company = cls.env["res.company"].create({"name": "Other %s" % token})
        cls.user = cls.env["res.users"].create(
            {
                "name": "MCP Retriever %s" % token,
                "login": "mcp_retriever_%s" % token,
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
                "company_id": cls.company.id,
                "company_ids": [(6, 0, [cls.company.id])],
            }
        )
        cls.api_key = (
            cls.env["res.users.apikeys"]
            .with_user(cls.user)
            ._generate(
                scope="rpc",
                name="MCP record retriever tests",
                expiration_date=fields.Datetime.now() + timedelta(days=1),
            )
        )
        cls.partners = cls.env["res.partner"].create(
            [
                {"name": "%s Page A" % token, "company_id": cls.company.id},
                {"name": "%s Page A" % token, "company_id": cls.company.id},
                {"name": "%s Page B" % token, "company_id": cls.company.id},
                {"name": "%s Page C" % token, "is_company": True},
            ]
        )
        cls.hidden = cls.env["res.partner"].create(
            {"name": "%s Hidden" % token, "company_id": cls.other_company.id}
        )
        cls.token = token

    def setUp(self):
        super().setUp()
        self.config.write(
            {
                "authentication_policy": "protected",
                "oauth_enabled": False,
                "allow_api_key": True,
                "mode": "stateless",
                "tool_mode": "all",
            }
        )

    def _call(self, arguments, authenticated=True):
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if authenticated:
            headers["Authorization"] = "Bearer %s" % self.api_key
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "odoo_record_retriever", "arguments": arguments},
        }
        return self.url_open("/mcp", data=json.dumps(payload), headers=headers)

    def _orm_text(self, domain, fields_, limit=100, offset=0, order=None):
        model = self.env["res.partner"].with_user(self.user)
        kwargs = {"domain": domain, "limit": limit, "offset": offset}
        if order:
            kwargs["order"] = order
        if fields_:
            rows = model.search_read(fields=fields_, **kwargs)
        else:
            rows = model.search(**kwargs).read()
        return str(json.loads(json.dumps(rows, default=str)))

    @mute_logger("odoo.addons.llm_mcp_server.mcp_json_dispatcher")
    def test_unauthenticated_call_is_rejected(self):
        response = self._call({"model": "res.partner"}, authenticated=False)
        self.assertEqual(response.status_code, 401, response.text)

    def test_compound_domain_pages_and_order_match_orm_as_bearer_user(self):
        ids = self.partners.ids + [self.hidden.id]
        domain = [
            "|",
            "&",
            ["id", "in", ids],
            "!",
            ["is_company", "=", True],
            ["name", "in", []],
        ]
        order = "name asc, id desc"
        seen = []
        for offset in (0, 2, 4):
            arguments = {
                "model": "res.partner",
                "domain": domain,
                "fields": ["id", "name"],
                "limit": 2,
                "offset": offset,
                "order": order,
            }
            response = self._call(arguments)
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()["result"]
            self.assertFalse(result["isError"], result)
            expected = self._orm_text(domain, ["id", "name"], 2, offset, order)
            self.assertEqual(result["content"][0]["text"], expected)
            seen.append(result["content"][0]["text"])
        self.assertEqual(seen[2], "[]")
        self.assertNotIn(self.hidden.name, seen[0] + seen[1])

    def test_zero_limit_and_omitted_fields_match_orm(self):
        domain = [["id", "in", self.partners.ids + [self.hidden.id]]]
        response = self._call(
            {"model": "res.partner", "domain": domain, "limit": 0, "order": "id asc"}
        )
        result = response.json()["result"]
        self.assertFalse(result["isError"], result)
        self.assertEqual(
            result["content"][0]["text"],
            self._orm_text(domain, [], limit=0, order="id asc"),
        )
        self.assertNotIn(self.hidden.name, result["content"][0]["text"])

    @mute_logger("odoo.addons.llm_mcp_server.models.llm_tool")
    def test_invalid_arguments_return_tool_errors(self):
        for arguments in (
            {"model": "res.partner", "offset": -1},
            {"model": "res.partner", "domain": "name = 'x'"},
            {"model": "res.partner", "domain": [["id", "in", "abc"]]},
            {"model": "res.partner", "order": "name; drop table res_partner"},
            {
                "model": "res.company",
                "domain": [["id", "=", self.other_company.id]],
                "fields": ["id", "name"],
            },
        ):
            with self.subTest(arguments=arguments):
                response = self._call(arguments)
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()["result"]
                if arguments["model"] == "res.company":
                    self.assertFalse(result["isError"], result)
                    self.assertEqual(result["content"][0]["text"], "[]")
                else:
                    self.assertTrue(result["isError"], result)
