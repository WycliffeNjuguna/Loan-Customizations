"""
Custom Loan Repayment Schedule Override
========================================
Intercepts make_repayment_schedule() on the LoanRepaymentSchedule doctype
and delegates to the appropriate calculation method based on the loan product's
custom_loan_calculation_method field.

Architecture:
  - This file contains only routing/dispatch logic.
  - Actual calculation methods live in schedule_methods.py.
  - To add a new method: register it in schedule_methods.SCHEDULE_METHODS dict.

Trigger Conditions:
  - custom_loan_calculation_method is set on the Loan Repayment Schedule
    (fetched from Loan -> Loan Product)

Fallback:
  - If no custom_loan_calculation_method is set, or the method is not found
    in the registry, the standard ERPNext reducing-balance logic runs unchanged.
  - A client-side override hook (override_loan_schedule_method in hooks.py)
    can handle methods outside the built-in registry.
"""

import frappe
from frappe import _
from frappe.utils import flt
from lending.loan_management.doctype.loan_repayment_schedule.loan_repayment_schedule import (
    LoanRepaymentSchedule,
)

from loan_customizations.overrides.schedule_methods import SCHEDULE_METHODS


class CustomLoanRepaymentSchedule(LoanRepaymentSchedule):
    """
    Extended LoanRepaymentSchedule with multi-method calculation support.

    Custom fields required on Loan Repayment Schedule (fetched from Loan):
        - custom_loan_calculation_method       (Data, fetched from Loan)
        - custom_monthly_interest_rate_        (Percent, fetched from Loan)
        - custom_arrears_carry_forward_scope   (Data, fetched from Loan)
    """

    def make_repayment_schedule(self, *args, **kwargs):
        self._sync_custom_fields_from_loan()
        method = self._get_calculation_method()

        if not method:
            # No custom method set — use standard ERPNext logic
            super().make_repayment_schedule(*args, **kwargs)
            return

        # Look up in built-in registry first
        handler = SCHEDULE_METHODS.get(method)

        if handler:
            handler(self)
            return

        # Not in registry — check for a client-provided override hook
        client_hook = frappe.get_hooks("override_loan_schedule_method")
        if client_hook:
            frappe.get_attr(client_hook[-1])(self, method)
            return

        # Method specified but not found anywhere — warn and fall back
        frappe.msgprint(
            _(
                "Loan calculation method <b>{0}</b> is not registered. "
                "Falling back to standard ERPNext schedule. "
                "Please register a handler in SCHEDULE_METHODS or via the "
                "override_loan_schedule_method hook."
            ).format(method),
            indicator="orange",
            title=_("Unknown Calculation Method"),
        )
        super().make_repayment_schedule(*args, **kwargs)

    # ── Helpers ──────────────────────────────────────────────────────────── #

    def _sync_custom_fields_from_loan(self):
        """
        Explicitly fetch custom fields from the linked Loan document.

        fetch_from is a client-side (browser) mechanism only — it does NOT run
        during server-side validation. Without this, custom_monthly_interest_rate_
        and custom_loan_calculation_method are always 0/blank server-side, which
        causes every schedule method to produce zero interest.
        """
        if not self.loan:
            return

        loan_fields = frappe.db.get_value(
            "Loan",
            self.loan,
            [
                "custom_loan_calculation_method",
                "custom_monthly_interest_rate_",
                "custom_arrears_carry_forward_scope",
            ],
            as_dict=True,
        )

        if not loan_fields:
            return

        if loan_fields.custom_loan_calculation_method is not None:
            self.custom_loan_calculation_method = loan_fields.custom_loan_calculation_method
        if loan_fields.custom_monthly_interest_rate_ is not None:
            self.custom_monthly_interest_rate_ = loan_fields.custom_monthly_interest_rate_
        if loan_fields.custom_arrears_carry_forward_scope is not None:
            self.custom_arrears_carry_forward_scope = loan_fields.custom_arrears_carry_forward_scope

    def _get_calculation_method(self):
        """
        Return the calculation method string, or None if we should use the
        standard ERPNext path.

        Priority:
          1. custom_loan_calculation_method field (set on Loan, fetched from
             Loan Product via custom fields)
          2. Legacy fallback: if no method field but custom_monthly_interest_rate_
             is set, treat as Equal Principal Installments (backward compat
             with the original override behaviour).
        """
        method = (getattr(self, "custom_loan_calculation_method", "") or "").strip()
        if method:
            return method

        # Legacy path: app only had Equal Principal before the method field
        if (
            self.repayment_method == "Repay Over Number of Periods"
            and self.repayment_periods
            and int(self.repayment_periods) > 0
            and flt(getattr(self, "custom_monthly_interest_rate_", 0)) > 0
        ):
            return "Equal Principal Installments"

        return None
