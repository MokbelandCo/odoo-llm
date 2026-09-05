"""Period close tools: pre-close checklist and lock date management"""

import logging

from odoo import _, models
from odoo.exceptions import UserError

from odoo.addons.llm_tool.decorators import llm_tool

_logger = logging.getLogger(__name__)

# Lock types exposed by the tools, mapped to the res.company fields that back
# them. Odoo 19 dropped the "non-advisers" period_lock_date in favour of the
# per-journal-type sale/purchase locks plus the irreversible hard lock.
LOCK_FIELD_MAP = {
    "all_users": "fiscalyear_lock_date",
    "tax": "tax_lock_date",
    "sale": "sale_lock_date",
    "purchase": "purchase_lock_date",
    "hard": "hard_lock_date",
}


class AccountPeriod(models.Model):
    _name = "account.tool.period"
    _inherit = "account.tool.mixin"
    _description = "LLM tools for period close operations"

    @llm_tool(read_only_hint=True, idempotent_hint=True)
    def account_check_period(
        self,
        date_from: str,
        date_to: str,
    ) -> dict:
        """Run pre-close checklist for a period

        Checks the readiness of a period for closing by counting
        draft moves, unreconciled items, and showing current lock dates.

        Args:
            date_from: Period start date (YYYY-MM-DD)
            date_to: Period end date (YYYY-MM-DD)

        Returns:
            Dictionary with checklist items and pass/fail status
        """
        Move = self.env["account.move"]
        MoveLine = self.env["account.move.line"]
        company = self.env.company

        # 1. Draft moves in period
        draft_count = Move.search_count(
            [
                ("state", "=", "draft"),
                ("date", ">=", date_from),
                ("date", "<=", date_to),
            ]
        )

        # 2. Unreconciled bank statement lines
        bank_journals = self.env["account.journal"].search(
            [("type", "in", ("bank", "cash", "credit"))]
        )
        unreconciled_bank = 0
        if bank_journals:
            unreconciled_bank = MoveLine.search_count(
                [
                    ("journal_id", "in", bank_journals.ids),
                    ("date", ">=", date_from),
                    ("date", "<=", date_to),
                    ("parent_state", "=", "posted"),
                    ("account_id.reconcile", "=", True),
                    ("reconciled", "=", False),
                ]
            )

        # 3. Unreconciled receivable/payable
        unreconciled_rp = MoveLine.search_count(
            [
                (
                    "account_id.account_type",
                    "in",
                    ("asset_receivable", "liability_payable"),
                ),
                ("date", ">=", date_from),
                ("date", "<=", date_to),
                ("parent_state", "=", "posted"),
                ("reconciled", "=", False),
            ]
        )

        # 4. Current lock dates
        lock_dates = self._get_lock_dates(company)

        checks = [
            {
                "check": "draft_moves",
                "description": "No draft moves in period",
                "count": draft_count,
                "passed": draft_count == 0,
            },
            {
                "check": "bank_reconciliation",
                "description": "All bank items reconciled",
                "count": unreconciled_bank,
                "passed": unreconciled_bank == 0,
            },
            {
                "check": "receivable_payable",
                "description": "Receivable/payable items reconciled",
                "count": unreconciled_rp,
                "passed": unreconciled_rp == 0,
                "info": "Warning only - not all items need reconciliation",
            },
        ]

        all_passed = all(c["passed"] for c in checks[:2])  # Only hard checks

        return {
            "date_from": date_from,
            "date_to": date_to,
            "company": company.name,
            "checks": checks,
            "lock_dates": lock_dates,
            "ready_to_close": all_passed,
            "message": "Period is ready for closing"
            if all_passed
            else "Some checks failed - review before closing",
        }

    def _get_lock_dates(self, company) -> dict:
        """Return the company lock dates as YYYY-MM-DD strings."""
        return {
            field_name: str(company[field_name]) if company[field_name] else None
            for field_name in LOCK_FIELD_MAP.values()
        }

    @llm_tool(destructive_hint=True)
    def account_set_lock_date(
        self,
        lock_date: str,
        lock_type: str = "all_users",
    ) -> dict:
        """Set accounting lock date

        Prevents modifications to journal entries on or before the lock
        date. Different lock types control which entries are affected.

        Args:
            lock_date: Lock date in YYYY-MM-DD format
            lock_type: Type of lock:
                       "all_users" - Global lock (fiscalyear_lock_date)
                       "tax" - Tax return lock (tax_lock_date)
                       "sale" - Sales entries lock (sale_lock_date)
                       "purchase" - Purchase entries lock (purchase_lock_date)
                       "hard" - Irreversible lock (hard_lock_date)

        Returns:
            Dictionary with updated lock dates
        """
        company = self.env.company

        if lock_type not in LOCK_FIELD_MAP:
            raise UserError(
                _("Invalid lock_type '%s'. Use: %s")
                % (lock_type, ", ".join(LOCK_FIELD_MAP.keys()))
            )

        field_name = LOCK_FIELD_MAP[lock_type]
        company.write({field_name: lock_date})

        return {
            "company": company.name,
            "lock_type": lock_type,
            "field": field_name,
            "lock_date": lock_date,
            "all_lock_dates": self._get_lock_dates(company),
            "message": f"Lock date '{lock_type}' set to {lock_date}",
        }
