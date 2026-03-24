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
  - repayment_method == "Repay Over Number of Periods"
  - repayment_periods > 0

Fallback:
  - If no custom_loan_calculation_method is set, or the method is not found
    in the registry, the standard ERPNext reducing-balance logic runs unchanged.

IMPORTANT FIX NOTE (March 2026):
  The base class make_repayment_schedule() accepts 8 positional arguments when
  called from make_customer_repayment_schedule(). Our override must accept
  *args, **kwargs and forward them on any super() fallback.

  Also, custom_monthly_interest_rate_ and custom_loan_calculation_method are
  defined with fetch_from on the Loan Repayment Schedule JSON. fetch_from is
  a CLIENT-SIDE only mechanism — it does NOT run during server-side validate().
  We must explicitly sync these fields from the linked Loan document before
  dispatching.
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
        """
        Override entry point. Accepts *args, **kwargs to match any signature
        the base class might call with (the lending app passes 8 positional
        args from make_customer_repayment_schedule).

        Steps:
          1. Sync custom fields from the linked Loan (fetch_from doesn't work
             server-side)
          2. Determine the calculation method
          3. Dispatch to the correct handler or fall back to base class
        """
        # ── Step 1: Sync custom fields from Loan ──
        self._sync_custom_fields_from_loan()

        # ── Step 2: Determine method ──
        method = self._get_calculation_method()

        if not method:
            # No custom method set — use standard ERPNext logic
            super().make_repayment_schedule(*args, **kwargs)
            return

        # ── Step 3: Dispatch ──
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

        # Method specified but not found anywhere — warn and fall back to standard
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

    # ── Field Sync ───────────────────────────────────────────────────────── #

    def _sync_custom_fields_from_loan(self):
        """
        Explicitly read custom fields from the linked Loan document.

        WHY: The custom fields on Loan Repayment Schedule use `fetch_from`
        in their JSON definition (e.g. fetch_from: "loan.custom_monthly_interest_rate_").
        fetch_from is a Frappe CLIENT-SIDE feature — it only triggers when a
        user changes the link field in the browser. During server-side
        operations (validate, on_submit, programmatic creation), these fields
        remain at their default value (0 or blank).

        This method fills them from the DB so that schedule_methods.py always
        has correct rate and method data.
        """
        if not self.loan:
            return

        # Fields to sync from Loan -> Loan Repayment Schedule
        fields_to_sync = [
            "custom_monthly_interest_rate_",
            "custom_loan_calculation_method",
            "custom_arrears_policy",
            "custom_arrears_carry_forward_scope",
        ]

        loan_values = frappe.db.get_value(
            "Loan",
            self.loan,
            fields_to_sync,
            as_dict=True,
        )

        if not loan_values:
            return

        for field in fields_to_sync:
            loan_val = loan_values.get(field)
            current_val = getattr(self, field, None)

            # Only overwrite if the current value is empty/zero and the loan has data
            if loan_val and (not current_val or current_val == 0):
                setattr(self, field, loan_val)

    # ── Helpers ──────────────────────────────────────────────────────────── #

    def _get_calculation_method(self):
        """
        Return the calculation method string, or None if we should use the
        standard ERPNext path.

        Priority:
          1. custom_loan_calculation_method field (set on Loan, fetched from
             Loan Product via custom fields)
          2. Legacy fallback: if no method field but custom_monthly_interest_rate_
             is set, treat as Equal Principal Installments (backward compat with
             the original override behaviour).
        """
        method = (getattr(self, "custom_loan_calculation_method", "") or "").strip()
        if method:
            return method

        # Legacy path: app only had Equal Principal before the method field was added
        if (
            self.repayment_method == "Repay Over Number of Periods"
            and self.repayment_periods
            and int(self.repayment_periods) > 0
            and flt(getattr(self, "custom_monthly_interest_rate_", 0)) > 0
        ):
            return "Equal Principal Installments"

        return None