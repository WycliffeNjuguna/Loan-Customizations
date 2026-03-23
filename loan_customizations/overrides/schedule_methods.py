"""
Loan Schedule Calculation Methods
==================================
Each method receives a LoanRepaymentSchedule document (self) and must:
  1. Clear self.repayment_schedule
  2. Populate rows with: payment_date, principal_amount, interest_amount,
     balance_loan_amount, total_payment
  3. Update self.monthly_repayment_amount and self.repayment_periods if needed

Registry pattern — add new methods to SCHEDULE_METHODS dict at the bottom.
"""

import math
import frappe
from frappe import _
from frappe.utils import add_months, getdate, flt, cint


# ═══════════════════════════════════════════════════════════════════════════ #
#  HELPERS
# ═══════════════════════════════════════════════════════════════════════════ #

def _get_payment_dates(doc):
    """Return a generator of payment dates based on repayment_start_date."""
    start = getdate(doc.repayment_start_date)
    i = 0
    while True:
        yield add_months(start, i)
        i += 1


def _append_schedule_row(doc, payment_date, principal, interest, balance):
    """Append a single row to the repayment_schedule child table."""
    doc.append("repayment_schedule", {
        "payment_date": payment_date,
        "principal_amount": flt(principal, 2),
        "interest_amount": flt(interest, 2),
        "balance_loan_amount": flt(balance, 2),
        "total_payment": flt(principal + interest, 2),
        "is_accrued": 0,
    })


# ═══════════════════════════════════════════════════════════════════════════ #
#  METHOD 1: EMI Flat Rate (Straight-Line Interest)
# ═══════════════════════════════════════════════════════════════════════════ #

def generate_emi_flat_rate(doc):
    """
    Total interest = principal × monthly_rate% × N months
    EMI = (principal + total_interest) / N
    Each period: interest portion is flat, principal portion is flat.
    """
    doc.repayment_schedule = []
    loan_amount = flt(doc.loan_amount)
    periods = cint(doc.repayment_periods)
    monthly_rate = flt(doc.custom_monthly_interest_rate_) / 100

    if periods <= 0:
        frappe.throw(_("Repayment Periods must be greater than 0 for EMI Flat Rate."))

    total_interest = flt(loan_amount * monthly_rate * periods, 2)
    emi = flt((loan_amount + total_interest) / periods, 2)
    flat_interest = flt(total_interest / periods, 2)
    flat_principal = flt(loan_amount / periods, 2)

    balance = loan_amount
    dates = _get_payment_dates(doc)

    for i in range(periods):
        payment_date = next(dates)
        # Last period: clear rounding remainder
        if i == periods - 1:
            principal = flt(balance, 2)
            interest = flt(total_interest - (flat_interest * (periods - 1)), 2)
        else:
            principal = flat_principal
            interest = flat_interest

        balance = flt(balance - principal, 2)
        _append_schedule_row(doc, payment_date, principal, interest, max(balance, 0))

    doc.monthly_repayment_amount = emi


# ═══════════════════════════════════════════════════════════════════════════ #
#  METHOD 2: EMI Reducing Balance (Standard Amortisation)
# ═══════════════════════════════════════════════════════════════════════════ #

def generate_emi_reducing_balance(doc):
    """
    Standard amortising EMI.
    EMI = P × r × (1+r)^n / ((1+r)^n - 1)
    """
    doc.repayment_schedule = []
    loan_amount = flt(doc.loan_amount)
    periods = cint(doc.repayment_periods)
    monthly_rate = flt(doc.custom_monthly_interest_rate_) / 100

    if periods <= 0:
        frappe.throw(_("Repayment Periods must be greater than 0 for EMI Reducing Balance."))

    if monthly_rate <= 0:
        # Zero interest fallback — just divide principal equally
        return _generate_zero_interest_core(doc)

    r = monthly_rate
    emi = flt(loan_amount * r * ((1 + r) ** periods) / (((1 + r) ** periods) - 1), 2)

    balance = loan_amount
    dates = _get_payment_dates(doc)

    for i in range(periods):
        payment_date = next(dates)
        interest = flt(balance * r, 2)

        if i == periods - 1:
            principal = flt(balance, 2)
            interest = flt(max(emi - principal, 0), 2)
        else:
            principal = flt(emi - interest, 2)

        balance = flt(balance - principal, 2)
        _append_schedule_row(doc, payment_date, principal, interest, max(balance, 0))

    doc.monthly_repayment_amount = emi


# ═══════════════════════════════════════════════════════════════════════════ #
#  METHOD 3: Equal Principal Installments
# ═══════════════════════════════════════════════════════════════════════════ #

def generate_equal_principal(doc):
    """
    Fixed principal each month = loan_amount / N.
    Interest on reducing balance each month.
    Total installment decreases over time.
    """
    doc.repayment_schedule = []
    loan_amount = flt(doc.loan_amount)
    periods = cint(doc.repayment_periods)
    monthly_rate = flt(doc.custom_monthly_interest_rate_) / 100

    if periods <= 0:
        frappe.throw(_("Repayment Periods must be greater than 0 for Equal Principal."))

    fixed_principal = flt(loan_amount / periods, 2)
    balance = loan_amount
    dates = _get_payment_dates(doc)

    for i in range(periods):
        payment_date = next(dates)
        interest = flt(balance * monthly_rate, 2)
        principal = fixed_principal if i < periods - 1 else flt(balance, 2)
        balance = flt(balance - principal, 2)

        _append_schedule_row(doc, payment_date, principal, interest, max(balance, 0))

    doc.monthly_repayment_amount = flt(fixed_principal + (loan_amount * monthly_rate), 2)


# ═══════════════════════════════════════════════════════════════════════════ #
#  METHOD 4: Zero Interest
# ═══════════════════════════════════════════════════════════════════════════ #

def generate_zero_interest(doc):
    """Principal only — EMI = loan_amount / N, zero interest."""
    _generate_zero_interest_core(doc)


def _generate_zero_interest_core(doc):
    """Internal zero-interest handler, also used as fallback."""
    doc.repayment_schedule = []
    loan_amount = flt(doc.loan_amount)
    periods = cint(doc.repayment_periods)

    if periods <= 0:
        frappe.throw(_("Repayment Periods must be greater than 0 for Zero Interest."))

    fixed_principal = flt(loan_amount / periods, 2)
    balance = loan_amount
    dates = _get_payment_dates(doc)

    for i in range(periods):
        payment_date = next(dates)
        principal = fixed_principal if i < periods - 1 else flt(balance, 2)
        balance = flt(balance - principal, 2)

        _append_schedule_row(doc, payment_date, principal, 0, max(balance, 0))

    doc.monthly_repayment_amount = fixed_principal


# ═══════════════════════════════════════════════════════════════════════════ #
#  METHOD 5: Edu Loans (K.R.J.B Edu-Credit)
# ═══════════════════════════════════════════════════════════════════════════ #

def generate_graduated_repayment(doc):
    """
    Zero-interest graduated/stepped repayment.

    Monthly deduction is determined by a slab table based on the outstanding
    balance at the start of each period. As the balance drops into lower
    brackets, the deduction decreases.

    The slab table is stored on the Loan Product as a child table:
    custom_graduated_repayment_slabs (Graduated Repayment Slab).

    The number of periods is dynamically computed (not user-specified).
    Supports both repayment methods:
      - "Repay Over Number of Periods": uses slab-based deduction (standard)
      - "Repay Fixed Amount per Period": uses the user-specified amount instead
        of the slab (overrides the graduated logic)
    """
    doc.repayment_schedule = []
    loan_amount = flt(doc.loan_amount)

    # Check if user specified a fixed amount override
    repayment_method = (getattr(doc, "repayment_method", "") or "").strip()
    fixed_amount = flt(getattr(doc, "monthly_repayment_amount", 0))

    use_fixed_amount = (
        repayment_method == "Repay Fixed Amount per Period"
        and fixed_amount > 0
    )

    if not use_fixed_amount:
        # Standard graduated: fetch slabs from Loan Product
        loan_product = doc.loan_product
        if not loan_product:
            frappe.throw(_("Loan Product is required for Edu Loans."))

        slabs = frappe.get_all(
            "Graduated Repayment Slab",
            filters={"parent": loan_product, "parenttype": "Loan Product"},
            fields=["balance_from", "balance_to", "monthly_deduction"],
            order_by="balance_from desc",
        )

        if not slabs:
            frappe.throw(
                _("No Edu Loans repayment slabs defined on Loan Product {0}. "
                  "Please add the slab table before generating the schedule."
                  ).format(loan_product)
            )

        # Sort slabs by balance_from descending for bracket lookup
        slabs = sorted(slabs, key=lambda s: flt(s.balance_from), reverse=True)

        # Validate loan amount against max slab
        max_slab_balance = max(flt(s.balance_to) for s in slabs)
        if loan_amount > max_slab_balance:
            frappe.msgprint(
                _("Loan amount {0} exceeds the maximum slab balance of {1}. "
                  "Using the highest slab deduction ({2}) for amounts above "
                  "the defined range."
                  ).format(
                    frappe.format_value(loan_amount, {"fieldtype": "Currency"}),
                    frappe.format_value(max_slab_balance, {"fieldtype": "Currency"}),
                    frappe.format_value(
                        flt(slabs[0].monthly_deduction),
                        {"fieldtype": "Currency"},
                    ),
                ),
                indicator="orange",
                title=_("Edu Loans Warning"),
            )

        def get_deduction(balance):
            """Find the monthly deduction for a given outstanding balance."""
            for slab in slabs:
                if flt(balance) >= flt(slab.balance_from):
                    return flt(slab.monthly_deduction)
            # Below lowest slab — use lowest slab's deduction
            return flt(slabs[-1].monthly_deduction)
    else:
        # Fixed amount override — no slabs needed
        def get_deduction(balance):
            return fixed_amount

    balance = loan_amount
    dates = _get_payment_dates(doc)
    period_count = 0
    max_periods = 600  # Safety limit (50 years)

    while balance > 0 and period_count < max_periods:
        payment_date = next(dates)
        deduction = get_deduction(balance)

        # Final payment: don't overshoot
        principal = min(deduction, flt(balance, 2))
        balance = flt(balance - principal, 2)

        _append_schedule_row(doc, payment_date, principal, 0, max(balance, 0))
        period_count += 1

    if balance > 0:
        frappe.msgprint(
            _("Warning: Graduated repayment schedule reached the {0}-period "
              "safety limit with {1} still outstanding."
              ).format(max_periods, frappe.format_value(balance, {"fieldtype": "Currency"})),
            indicator="red",
            title=_("Schedule Limit Reached"),
        )

    # Update doc fields with computed values
    doc.repayment_periods = period_count
    if period_count > 0:
        doc.monthly_repayment_amount = flt(
            doc.repayment_schedule[0].principal_amount, 2
        )


# ═══════════════════════════════════════════════════════════════════════════ #
#  METHOD REGISTRY
# ═══════════════════════════════════════════════════════════════════════════ #

SCHEDULE_METHODS = {
    "EMI Flat Rate": generate_emi_flat_rate,
    "EMI Reducing Balance": generate_emi_reducing_balance,
    "Equal Principal Installments": generate_equal_principal,
    "Zero Interest": generate_zero_interest,
    "Edu Loans": generate_graduated_repayment,
}
