import inspect
import json
import uuid

from pydantic import ValidationError

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.llm_tool.odoo_domain import normalize_domain_json

from .common import LLMToolCase


def _json_rows(rows):
    return json.loads(json.dumps(rows, default=str))


@tagged("post_install", "-at_install")
class TestOdooRecordRetriever(LLMToolCase):
    """Record retrieval through llm.tool.execute and the generated MCP schema."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tool = cls.env["llm.tool"].search(
            [("name", "=", "odoo_record_retriever")], limit=1
        )
        if not cls.tool:
            raise AssertionError("odoo_record_retriever tool record is missing")

    def _token(self):
        return uuid.uuid4().hex

    def _execute(self, user=None, **arguments):
        tool = self.tool if user is None else self.tool.with_user(user)
        return tool.execute(arguments)

    def _orm_rows(self, model, domain, fields, limit=100, offset=0, order=None, user=None):
        model_obj = self.env[model] if user is None else self.env[model].with_user(user)
        search_kwargs = {"domain": domain, "limit": limit, "offset": offset}
        if order:
            search_kwargs["order"] = order
        if fields:
            rows = model_obj.search_read(fields=fields, **search_kwargs)
        else:
            rows = model_obj.search(**search_kwargs).read()
        return _json_rows(rows)

    def test_signature_defaults_and_single_registration(self):
        params = inspect.signature(type(self.tool).odoo_record_retriever_execute).parameters
        self.assertEqual(params["limit"].default, 100)
        self.assertEqual(params["offset"].default, 0)
        self.assertIsNone(params["order"].default)
        self.assertEqual(
            self.env["llm.tool"].search_count([("name", "=", "odoo_record_retriever")]),
            1,
        )

    def test_mcp_schema_accepts_full_domain_offset_and_order(self):
        from mcp.server.fastmcp.utilities.func_metadata import func_metadata

        schema = self.tool.get_input_schema()
        properties = schema["properties"]
        self.assertEqual(properties["fields"]["items"]["type"], "string")
        self.assertEqual(properties["limit"]["default"], 100)
        self.assertEqual(properties["limit"]["minimum"], 0)
        self.assertEqual(properties["offset"]["default"], 0)
        self.assertEqual(properties["offset"]["minimum"], 0)
        self.assertIn("id", properties["order"]["description"])
        domain_schema = json.dumps(properties["domain"])
        self.assertIn('"|"', domain_schema)
        self.assertIn('"&"', domain_schema)
        self.assertIn('"!"', domain_schema)

        definition = self.tool.get_tool_definition()
        self.assertIn("offset", definition["inputSchema"]["properties"])
        self.assertIn("order", definition["inputSchema"]["properties"])

        meta = func_metadata(
            type(self.tool).odoo_record_retriever_execute, skip_names=["self"]
        )
        domain = [
            "|",
            ["name", "in", ["A", "B"]],
            "&",
            ["active", "=", True],
            ["parent_id", "=", None],
        ]
        validated = meta.arg_model.model_validate(
            {
                "model": "res.partner",
                "domain": domain,
                "fields": ["id", "name"],
                "limit": 2,
                "offset": 1,
                "order": "name asc, id asc",
            }
        )
        dumped = validated.model_dump()
        self.assertEqual(dumped["domain"], domain)
        self.assertIs(dumped["domain"][3][2], True)
        self.assertIsNone(dumped["domain"][4][2])
        self.assertEqual(dumped["domain"][1][2], ["A", "B"])

        rich = [
            ["balance", ">=", 1.5],
            ["id", "in", [1, 2]],
            ["active", "=", False],
        ]
        self.assertEqual(normalize_domain_json(rich), rich)
        self.assertIs(normalize_domain_json(rich)[2][2], False)
        self.assertIsInstance(normalize_domain_json(rich)[0][2], float)
        self.assertEqual(normalize_domain_json([["name", "in", []]])[0][2], [])

        with self.assertRaises(ValidationError):
            meta.arg_model.model_validate(
                {"model": "res.partner", "domain": "name = 'A'"}
            )
        with self.assertRaises(ValidationError):
            meta.arg_model.model_validate({"model": "res.partner", "offset": -1})
        with self.assertRaises(ValidationError):
            meta.arg_model.model_validate({"model": "res.partner", "limit": -5})

    def test_execute_preserves_operand_types_and_matches_orm(self):
        token = self._token()
        parent = self.env["res.partner"].create({"name": "Parent %s" % token})
        child = self.env["res.partner"].create(
            {"name": "Child %s" % token, "parent_id": parent.id}
        )
        other = self.env["res.partner"].create({"name": "Other %s" % token})
        archived = self.env["res.partner"].create(
            {"name": "Archived %s" % token, "active": False}
        )
        domain = [
            "|",
            ["id", "in", [parent.id, child.id, other.id, archived.id]],
            ["name", "in", []],
        ]
        arguments = {
            "model": "res.partner",
            "domain": domain,
            "fields": ["id", "name"],
            "limit": 10,
            "offset": 0,
            "order": "id asc",
        }
        model = self.tool.get_pydantic_model_from_signature(
            type(self.tool).odoo_record_retriever_execute
        )
        dumped_domain = model(**arguments).model_dump()["domain"]
        self.assertEqual(dumped_domain, domain)
        self.assertEqual(dumped_domain[1][2], [parent.id, child.id, other.id, archived.id])
        self.assertIsInstance(dumped_domain[1][2][0], int)
        self.assertEqual(dumped_domain[2][2], [])

        rows = self._execute(**arguments)
        self.assertEqual(
            rows,
            self._orm_rows(
                "res.partner", domain, ["id", "name"], limit=10, offset=0, order="id asc"
            ),
        )
        self.assertNotIn(archived.id, [row["id"] for row in rows])

    def test_domain_operators_logic_and_nulls(self):
        token = self._token()
        parent = self.env["res.partner"].create({"name": "Parent %s" % token})
        alpha = self.env["res.partner"].create(
            {"name": "Alpha %s" % token, "parent_id": parent.id}
        )
        beta = self.env["res.partner"].create(
            {"name": "Beta %s" % token, "parent_id": parent.id}
        )
        orphan = self.env["res.partner"].create({"name": "Orphan %s" % token})
        archived = self.env["res.partner"].create(
            {"name": "Archived %s" % token, "active": False}
        )
        cases = {
            "in_ids": [["id", "in", [alpha.id, beta.id]]],
            "not_in_ids": [
                ["name", "ilike", token],
                ["id", "not in", [beta.id, parent.id]],
            ],
            "in_strings": [["name", "in", [alpha.name, orphan.name]]],
            "not_in_strings": [
                ["name", "ilike", token],
                ["name", "not in", [alpha.name]],
            ],
            "empty_in": [["id", "in", []], ["name", "ilike", token]],
            "empty_not_in": [["id", "=", orphan.id], ["name", "not in", []]],
            "or": ["|", ["id", "=", alpha.id], ["id", "=", beta.id]],
            "and": ["&", ["parent_id", "=", parent.id], ["name", "ilike", "Alpha"]],
            "implicit_and": [["parent_id", "=", parent.id], ["name", "ilike", "Beta"]],
            "not": ["&", ["name", "ilike", token], "!", ["id", "=", alpha.id]],
            "nested": [
                "&",
                "|",
                ["id", "=", alpha.id],
                ["id", "=", orphan.id],
                ["name", "ilike", token],
            ],
            "dotted": [["parent_id.name", "=", parent.name]],
            "boolean_true": [["name", "ilike", token], ["active", "=", True]],
            "boolean_false": [["name", "=", archived.name], ["active", "=", False]],
            "null": [["name", "ilike", token], ["parent_id", "=", None]],
            "false_null": [["name", "ilike", token], ["parent_id", "=", False]],
            "ilike": [["name", "ilike", "alpha %s" % token]],
            "child_of": [["id", "child_of", parent.id]],
            "any": [["child_ids", "any", [["name", "=", alpha.name]]]],
            "ge": [["id", ">=", beta.id], ["name", "ilike", token]],
        }
        for label, domain in cases.items():
            with self.subTest(label=label):
                rows = self._execute(
                    model="res.partner",
                    domain=domain,
                    fields=["id", "name"],
                    limit=20,
                    offset=0,
                    order="id asc",
                )
                self.assertEqual(
                    rows,
                    self._orm_rows(
                        "res.partner",
                        domain,
                        ["id", "name"],
                        limit=20,
                        order="id asc",
                    ),
                )
        self.assertEqual(
            [
                row["id"]
                for row in self._execute(
                    model="res.partner",
                    domain=[["name", "ilike", token], ["parent_id", "=", None]],
                    fields=["id"],
                    order="id asc",
                )
            ],
            [parent.id, orphan.id],
        )

    def test_selected_and_omitted_fields_with_paging(self):
        token = self._token()
        names = ["Page A", "Page A", "Page B", "Page C", "Page D"]
        partners = self.env["res.partner"].create(
            [{"name": "%s %s" % (token, name)} for name in names]
        )
        domain = [["id", "in", partners.ids]]
        order = "name asc, id asc"
        full = self._execute(
            model="res.partner",
            domain=domain,
            fields=["id", "name"],
            limit=10,
            offset=0,
            order=order,
        )
        pages = [
            self._execute(
                model="res.partner",
                domain=domain,
                fields=["id", "name"],
                limit=2,
                offset=offset,
                order=order,
            )
            for offset in (0, 2, 4)
        ]
        paged_ids = [row["id"] for page in pages for row in page]
        self.assertEqual(paged_ids, [row["id"] for row in full])
        self.assertEqual(len(paged_ids), len(set(paged_ids)))
        self.assertEqual(
            self._execute(
                model="res.partner",
                domain=domain,
                fields=["id", "name"],
                limit=2,
                offset=50,
                order=order,
            ),
            [],
        )
        tied_asc = [
            row["id"]
            for row in full
            if row["name"].endswith("Page A")
        ]
        tied_desc = [
            row["id"]
            for row in self._execute(
                model="res.partner",
                domain=domain,
                fields=["id", "name"],
                limit=10,
                order="name asc, id desc",
            )
            if row["name"].endswith("Page A")
        ]
        self.assertEqual(tied_asc, list(reversed(tied_desc)))
        self.assertEqual(len(tied_asc), 2)
        self.assertEqual(set(full[0]), {"id", "name"})

        omitted = self._execute(
            model="res.partner",
            domain=[["id", "=", partners[0].id]],
            limit=1,
            offset=0,
            order="id asc",
        )
        self.assertEqual(
            omitted,
            self._orm_rows(
                "res.partner",
                [["id", "=", partners[0].id]],
                [],
                limit=1,
                order="id asc",
            ),
        )
        self.assertIn("display_name", omitted[0])
        self.assertEqual(
            self._execute(
                model="res.partner",
                domain=domain,
                fields=["id", "name"],
                limit=10,
                order="name desc, id desc",
            ),
            self._orm_rows(
                "res.partner",
                domain,
                ["id", "name"],
                limit=10,
                order="name desc, id desc",
            ),
        )
        self.assertEqual(
            self._execute(
                model="res.partner",
                domain=[["id", "=", partners[0].id]],
                fields=["id", "name"],
                limit=5,
            ),
            self._orm_rows(
                "res.partner",
                [["id", "=", partners[0].id]],
                ["id", "name"],
                limit=5,
            ),
        )

    def test_malformed_domain_order_and_pagination(self):
        partner = self.env["res.partner"].create({"name": "Bad %s" % self._token()})
        with self.assertRaises(ValueError):
            self._execute(
                model="res.partner", domain=["|", ["name", "=", partner.name]]
            )
        with self.assertRaises(ValueError) as missing_field:
            self._execute(
                model="res.partner", domain=[["no_such_field_zzz", "=", 1]]
            )
        self.assertIn("no_such_field_zzz", str(missing_field.exception).lower())
        with self.assertRaises(ValueError) as bad_operator:
            self._execute(
                model="res.partner", domain=[["name", "not-an-operator", "x"]]
            )
        self.assertIn("not-an-operator", str(bad_operator.exception))
        with self.assertRaises(ValueError) as bad_value:
            self._execute(model="res.partner", domain=[["id", "in", "abc"]])
        self.assertIn("list", str(bad_value.exception))
        with self.assertRaises(ValidationError):
            self._execute(model="res.partner", domain="name = 'A'")
        with self.assertRaises(ValidationError):
            self._execute(model="res.partner", offset=-1)
        with self.assertRaises(ValidationError):
            self._execute(model="res.partner", limit=-1)
        with self.assertRaises(ValueError):
            self.tool.odoo_record_retriever_execute(model="res.partner", offset=-1)
        with self.assertRaises(ValueError):
            self.tool.odoo_record_retriever_execute(
                model="res.partner", fields="name"
            )
        with self.assertRaises(UserError):
            self._execute(model="res.partner", order="name; drop table res_partner")
        with self.assertRaises(UserError):
            self._execute(model="res.partner", order="")
        with self.assertRaises(ValueError) as bad_order_field:
            self._execute(
                model="res.partner",
                domain=[["id", "=", partner.id]],
                order="not_a_real_field asc",
            )
        self.assertIn("not_a_real_field", str(bad_order_field.exception))
        self.assertEqual(
            self._execute(
                model="res.partner",
                domain=[["name", "=", "missing-%s" % self._token()]],
                fields=["id"],
            ),
            [],
        )

    def test_restricted_company_and_field_access(self):
        token = self._token()
        other_company = self.env["res.company"].create({"name": "Other %s" % token})
        limited = self.env["res.users"].create(
            {
                "name": "Retriever Limited %s" % token,
                "login": "retriever_limited_%s" % token,
                "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
                "company_id": self.env.company.id,
                "company_ids": [(6, 0, [self.env.company.id])],
            }
        )
        domain = [["id", "in", [self.env.company.id, other_company.id]]]
        rows = self._execute(
            user=limited,
            model="res.company",
            domain=domain,
            fields=["id", "name"],
            order="id asc",
        )
        self.assertIn(self.env.company.id, [row["id"] for row in rows])
        self.assertNotIn(other_company.id, [row["id"] for row in rows])
        self.assertEqual(
            rows,
            self._orm_rows(
                "res.company",
                domain,
                ["id", "name"],
                order="id asc",
                user=limited,
            ),
        )
        with self.assertRaises(AccessError):
            self._execute(
                user=limited,
                model="res.users.identitycheck",
                domain=[],
                fields=["id", "request"],
            )

    def test_refresh_clears_stale_schema_without_a_second_tool(self):
        self.tool.write(
            {
                "description": "Retrieve records with a limit only.",
                "input_schema": json.dumps(
                    {
                        "type": "object",
                        "properties": {
                            "model": {"type": "string"},
                            "domain": {"type": "array"},
                            "limit": {"type": "integer"},
                        },
                        "required": ["model"],
                    }
                ),
            }
        )
        self.assertNotIn("offset", self.tool.get_input_schema()["properties"])
        self.tool._refresh_record_retriever_contract()
        self.assertFalse(self.tool.input_schema)
        self.assertIn("offset", self.tool.description)
        self.assertIn("id", self.tool.description)
        refreshed = self.tool.get_input_schema()
        self.assertIn("offset", refreshed["properties"])
        self.assertIn("order", refreshed["properties"])
        self.assertEqual(
            self.env["llm.tool"].search_count([("name", "=", "odoo_record_retriever")]),
            1,
        )
