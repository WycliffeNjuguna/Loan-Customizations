"""
Loan Validation Hooks
======================
Handles:
  1. Repayment Method either/or logic:
     - "Repay Fixed Amount per Period" → requires monthly_repayment_amount,
       auto-computes repayment_periods
     - "Repay Over Number of Periods" → requires repayment_periods,
       clears monthly_repayment_amount for auto-computation
  2. Edu Loans validation:
     - Ensures slabs exist on the Loan Product
     - Forces interest rate to 0
  3. Zero Interest validation:
     - Forces interest rate to 0
  4. Fetches custom fields from Loan Product to Loan
"""

import math
import frappe
from frappe import _
from frappe.utils import flt, cint


def validate_loan(doc, method=None):
    """Called on Loan.validate via doc_events hook."""
    _fetch_custom_fields_from_product(doc)
    _validate_repayment_method(doc)
    _validate_graduated_repayment_on_loan(doc)
    _validate_zero_interest(doc)


def validate_loan_repayment_schedule(doc, method=None):
    """Called on Loan Repayment Schedule.validate via doc_events hook."""
    _validate_repayment_method(doc)


# ═══════════════════════════════════════════════════════════════════════════ #
#  Fetch custom fields from Loan Product to Loan
# ═══════════════════════════════════════════════════════════════════════════ #

def _fetch_custom_fields_from_product(doc):
    """
    Copy custom fields from the selected Loan Product to the Loan doc.
    This ensures the schedule override and validation have access to
    the calculation method, arrears policy, etc.
    """
    if not doc.loan_product:
        return

    product = frappe.get_cached_doc("Loan Product", doc.loan_product)

    field_map = {
        "custom_loan_calculation_method": "custom_loan_calculation_method",
        "custom_monthly_interest_rate_": "custom_monthly_interest_rate_",
        "custom_arrears_policy": "custom_arrears_policy",
        "custom_arrears_carry_forward_scope": "custom_arrears_carry_forward_scope",
    }

    for product_field, loan_field in field_map.items():
        value = getattr(product, product_field, None)
        if value is not None:
            setattr(doc, loan_field, value)


# ═══════════════════════════════════════════════════════════════════════════ #
#  Repayment Method Either/Or
# ═══════════════════════════════════════════════════════════════════════════ #

def _validate_repayment_method(doc):
    """
    Enforce either/or logic for repayment method:
      - "Repay Fixed Amount per Period": amount required, periods auto-computed
      - "Repay Over Number of Periods": periods required, amount auto-computed
    """
    repayment_method = (getattr(doc, "repayment_method", "") or "").strip()
    if not repayment_method:
        return

    loan_amount = flt(doc.loan_amount)
    calc_method = (getattr(doc, "custom_loan_calculation_method", "") or "").strip()

    if repayment_method == "Repay Fixed Amount per Period":
        amount = flt(doc.monthly_repayment_amount)

        if amount <= 0:
            frappe.throw(
                _("Monthly Repayment Amount is required when Repayment Method "
                  "is 'Repay Fixed Amount per Period'."),
                title=_("Missing Repayment Amount"),
            )

        if loan_amount > 0 and amount > 0:
            # Auto-compute periods (ceiling to ensure full repayment)
            # For graduated repayment, periods are computed by the schedule
            # generator, so we skip auto-computation here
            if calc_method != "Edu Loans":
                computed_periods = math.ceil(loan_amount / amount)
                doc.repayment_periods = computed_periods

    elif repayment_method == "Repay Over Number of Periods":
        periods = cint(doc.repayment_periods)

        if periods <= 0 and calc_method != "Edu Loans":
            frappe.throw(
                _("Repayment Period in Months is required when Repayment Method "
                  "is 'Repay Over Number of Periods'."),
                title=_("Missing Repayment Periods"),
            )


# ═══════════════════════════════════════════════════════════════════════════ #
#  Edu Loans Validation
# ═══════════════════════════════════════════════════════════════════════════ #

def _validate_graduated_repayment_on_loan(doc):
    """
    When the calculation method is Edu Loans:
      - Verify slabs exist on the Loan Product
      - Force interest to 0
    """
    calc_method = (getattr(doc, "custom_loan_calculation_method", "") or "").strip()
    if calc_method != "Edu Loans":
        return

    if not doc.loan_product:
        return

    # Check that slabs exist
    slab_count = frappe.db.count(
        "Graduated Repayment Slab",
        filters={"parent": doc.loan_product, "parenttype": "Loan Product"},
    )

    if not slab_count:
        frappe.throw(
            _("Loan Product {0} uses Edu Loans but has no "
              "repayment slabs defined. Please add the Edu Loans "
              "Slabs table on the Loan Product first."
              ).format(frappe.bold(doc.loan_product)),
            title=_("Missing Edu Loans Repayment Slabs"),
        )

    # Force zero interest
    doc.rate_of_interest = 0
    doc.custom_monthly_interest_rate_ = 0


# ═══════════════════════════════════════════════════════════════════════════ #
#  Zero Interest Validation
# ═══════════════════════════════════════════════════════════════════════════ #

def _validate_zero_interest(doc):
    """
    When the calculation method is Zero Interest, enforce 0% rate.
    """
    calc_method = (getattr(doc, "custom_loan_calculation_method", "") or "").strip()
    if calc_method != "Zero Interest":
        return

    doc.rate_of_interest = 0
    doc.custom_monthly_interest_rate_ = 0
