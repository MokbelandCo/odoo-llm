import json

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.llm_tool.record_reference import (
    RECORD_REFERENCE_TYPE,
    is_record_reference,
    iter_record_references,
    record_backend_path,
    record_reference,
)


@tagged("post_install", "-at_install")
class TestRecordReference(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param(
            "web.base.url", "https://erp.example.com"
        )
        cls.partner = cls.env["res.partner"].create({"name": "Reference Partner"})

    def test_contract_fields(self):
        ref = record_reference(self.partner)
        self.assertEqual(
            ref,
            {
                "type": RECORD_REFERENCE_TYPE,
                "model": "res.partner",
                "id": self.partner.id,
                "label": "Reference Partner",
                "url": "https://erp.example.com/web#model=res.partner"
                f"&id={self.partner.id}&view_type=form",
            },
        )
        self.assertTrue(is_record_reference(ref))

    def test_plain_json_for_external_clients(self):
        """MCP clients get the reference as plain JSON with a usable URL."""
        payload = json.loads(json.dumps({"contact": record_reference(self.partner)}))
        self.assertTrue(payload["contact"]["url"].startswith("https://"))
        self.assertNotIn("link_token", payload["contact"])

    def test_custom_label_is_single_line_and_bounded(self):
        ref = record_reference(self.partner, label="  Line one\nline   two  ")
        self.assertEqual(ref["label"], "Line one line two")
        long_ref = record_reference(self.partner, label="x" * 500)
        self.assertEqual(len(long_ref["label"]), 200)

    def test_empty_recordset_returns_none(self):
        self.assertIsNone(record_reference(self.env["res.partner"]))

    def test_non_http_base_url_falls_back_to_relative_path(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "web.base.url", "javascript:alert(1)"
        )
        ref = record_reference(self.partner)
        self.assertEqual(
            ref["url"], record_backend_path("res.partner", self.partner.id)
        )

    def test_iter_finds_nested_references(self):
        other = self.env["res.partner"].create({"name": "Other"})
        result = {
            "a": record_reference(self.partner),
            "items": [{"b": record_reference(other)}, "text", 3],
            "fake": {"type": RECORD_REFERENCE_TYPE, "model": "res.partner", "id": "1"},
        }
        found = list(iter_record_references(result))
        self.assertEqual([ref["id"] for ref in found], [self.partner.id, other.id])
