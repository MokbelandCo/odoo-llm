from unittest.mock import patch

from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, new_test_user, tagged

from odoo.addons.llm_thread.controllers.main import LLMThreadController


@tagged("post_install", "-at_install")
class TestBrowserCompanyContext(TransactionCase):
    """The chat stream uses the same company context as a normal RPC."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.east = cls.env["res.company"].create({"name": "East Browser Co"})
        cls.west = cls.env["res.company"].create({"name": "West Browser Co"})
        cls.secret = cls.env["res.company"].create({"name": "Secret Browser Co"})
        cls.user = new_test_user(
            cls.env,
            login="llm_browser_company",
            groups="base.group_user",
            company_id=cls.east.id,
            company_ids=[(6, 0, [cls.east.id, cls.west.id])],
        )

    def test_parser_keeps_switcher_order_and_drops_junk(self):
        parsed = LLMThreadController._parse_allowed_company_ids(
            f" {self.west.id}, {self.east.id}, nope, {self.west.id} "
        )
        self.assertEqual(parsed, [self.west.id, self.east.id])
        self.assertEqual(LLMThreadController._parse_allowed_company_ids(None), [])
        self.assertEqual(LLMThreadController._parse_allowed_company_ids(""), [])

    def test_browser_list_sets_main_company_and_checked_companies(self):
        raw = f"{self.west.id},{self.east.id}"
        env = LLMThreadController._env_with_browser_companies(
            self.env(user=self.user.id, context={"lang": "en_US"}),
            raw,
        )
        self.assertEqual(env.context["allowed_company_ids"], [self.west.id, self.east.id])
        self.assertEqual(env.company.id, self.west.id)
        self.assertEqual(env.companies.ids, [self.west.id, self.east.id])

    def test_missing_list_leaves_the_user_record_in_charge(self):
        env = LLMThreadController._env_with_browser_companies(
            self.env(user=self.user.id, context={"lang": "en_US"}),
            None,
        )
        self.assertNotIn("allowed_company_ids", env.context)
        self.assertEqual(env.company.id, self.east.id)
        self.assertEqual(set(env.companies.ids), {self.east.id, self.west.id})

    def test_company_outside_the_user_record_is_rejected(self):
        env = LLMThreadController._env_with_browser_companies(
            self.env(user=self.user.id, context={"lang": "en_US"}),
            str(self.secret.id),
        )
        with self.assertRaises(AccessError):
            env.companies

    def test_tool_call_receives_the_browser_company_context(self):
        company_ids = [self.west.id, self.east.id]
        env = self.env(context=dict(self.env.context, allowed_company_ids=company_ids))
        tool = env["llm.tool"].create({
            "name": "browser_company_probe",
            "description": "Probe",
            "implementation": "function",
        })
        thread = type("Thread", (), {})()
        thread.tool_ids = tool
        thread._get_effective_tools = lambda: tool
        captured = {}

        def fake_execute(tool_record, parameters):
            captured["allowed"] = list(tool_record.env.context["allowed_company_ids"])
            captured["main"] = tool_record.env.company.id
            return {"ok": True}

        with patch.object(type(env["llm.tool"]), "execute", fake_execute):
            result = env["mail.message"]._execute_tool_with_context(tool.name, "{}", thread)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(captured["allowed"], company_ids)
        self.assertEqual(captured["main"], self.west.id)
