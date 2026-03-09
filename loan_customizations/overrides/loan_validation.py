"""
Loan & Loan Product Validation Hooks
======================================
Enforces business rules related to the custom_loan_calculation_method field:

  1. validate_loan_calculation_method (Loan.validate)
     - If method is "Zero Interest", zero out rate_of_interest and
       custom_monthly_interest_rate_ with a user-facing warning.
     - If method is "Equal Principal Installments" but arrears policy is not
       set to carry-forward, log an informational message.

  2. validate_loan_product_calculation_method (Loan Product.validate)
     - Auto-set arrears_policy defaults based on method selection.
     - Warn if Zero Interest is selected but a non-zero rate is configured.
     - Warn if Equal Principal is selected but arrears_policy is not
       Partial Carry Forward.
"""

import frappe
from frappe import _
from frappe.utils import flt

from loan_customizations.overrides.schedule_methods import CARRY_FORWARD_METHODS


# ─────────────────────────────────────────────────────────────────────────────
# Loan.validate hook
# ─────────────────────────────────────────────────────────────────────────────

def validate_loan_calculation_method(doc, method=None):
    """
    Called on Loan validate. Enforces:
      - Zero Interest: rate fields are forced to 0.
      - Equal Principal: checks carry-forward policy is set appropriately.
    """
    calc_method = (getattr(doc, "custom_loan_calculation_method", "") or "").strip()

    if calc_method == "Zero Interest":
        _enforce_zero_interest(doc)

    if calc_method in CARRY_FORWARD_METHODS:
        _check_carry_forward_policy(doc)


def _enforce_zero_interest(doc):
    """Hard-zero the interest rate fields and raise if someone tries to override."""
    annual_rate = flt(getattr(doc, "rate_of_interest", 0))
    monthly_rate = flt(getattr(doc, "custom_monthly_interest_rate_", 0))

    if annual_rate != 0 or monthly_rate != 0:
        # Zero them out and warn — do not hard-block (allows admin corrections)
        doc.rate_of_interest = 0
        doc.custom_monthly_interest_rate_ = 0
        frappe.msgprint(
            _(
                "Interest rate has been set to <b>0%</b>: this loan product uses the "
                "<b>Zero Interest</b> calculation method. No interest will be charged."
            ),
            indicator="orange",
            alert=True,
        )


def _check_carry_forward_policy(doc):
    """Inform the user if carry-forward scope is not configured."""
    scope = (getattr(doc, "custom_arrears_carry_forward_scope", "") or "").strip()
    if not scope:
        frappe.msgprint(
            _(
                "This loan uses the <b>Equal Principal Installments</b> method. "
                "Arrears Carry-Forward Scope is not set on the Loan Product — "
                "defaulting to <b>Both</b> (interest + principal carry forward)."
            ),
            indicator="blue",
            alert=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Loan Product.validate hook
# ─────────────────────────────────────────────────────────────────────────────

def validate_loan_product_calculation_method(doc, method=None):
    """
    Called on Loan Product validate. Enforces defaults and warns about
    inconsistent configurations.
    """
    calc_method = (getattr(doc, "custom_loan_calculation_method", "") or "").strip()

    if not calc_method:
        return

    # Auto-defaults for arrears policy
    _auto_set_arrears_policy(doc, calc_method)

    # Zero Interest specific checks
    if calc_method == "Zero Interest":
        _warn_if_nonzero_rate_on_product(doc)

    # Equal Principal checks
    if calc_method in CARRY_FORWARD_METHODS:
        _warn_if_missing_carry_forward(doc)
        # Ensure scope has a value
        if not (getattr(doc, "custom_arrears_carry_forward_scope", "") or "").strip():
            doc.custom_arrears_carry_forward_scope = "Both"


def _auto_set_arrears_policy(doc, calc_method):
    """Set the arrears policy default when a calculation method is chosen."""
    current_policy = (getattr(doc, "custom_arrears_policy", "") or "").strip()

    if calc_method in CARRY_FORWARD_METHODS:
        # Equal Principal → default to carry forward if not already set
        if not current_policy:
            doc.custom_arrears_policy = "Partial Carry Forward"
    else:
        # All other methods → default to non-carry-forward if not already set
        if not current_policy:
            doc.custom_arrears_policy = "Partial Non-Carry Forward"


def _warn_if_nonzero_rate_on_product(doc):
    """Warn if Zero Interest product has a non-zero rate configured."""
    annual_rate = flt(getattr(doc, "rate_of_interest", 0))
    monthly_rate = flt(getattr(doc, "custom_monthly_interest_rate_", 0))

    if annual_rate > 0 or monthly_rate > 0:
        frappe.msgprint(
            _(
                "This Loan Product uses <b>Zero Interest</b> method but has a non-zero "
                "interest rate configured ({0}% annual / {1}% monthly). "
                "The rate will be ignored when generating repayment schedules."
            ).format(annual_rate, monthly_rate),
            indicator="orange",
            title=_("Zero Interest Rate Conflict"),
        )


def _warn_if_missing_carry_forward(doc):
    """Warn if Equal Principal product does not have carry-forward arrears policy."""
    policy = (getattr(doc, "custom_arrears_policy", "") or "").strip()
    if policy and policy != "Partial Carry Forward":
        frappe.msgprint(
            _(
                "<b>Equal Principal Installments</b> loans typically use "
                "<b>Partial Carry Forward</b> arrears policy. "
                "The current setting is <b>{0}</b>. Please confirm this is intentional."
            ).format(policy),
            indicator="orange",
            title=_("Arrears Policy Mismatch"),
        )
