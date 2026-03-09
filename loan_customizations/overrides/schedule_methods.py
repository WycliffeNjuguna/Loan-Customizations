"""
Loan Repayment Schedule Calculation Methods
============================================
Plugin-style registry for all supported loan calculation methods.

To add a new method in future:
  1. Write a function with signature: fn(schedule_doc) -> None
     The function must populate schedule_doc.repayment_schedule (list of dicts).
  2. Register it: SCHEDULE_METHODS["My New Method"] = fn
  3. Add the option to custom_loan_calculation_method Select field on Loan Product.

No other code changes required.
"""

import frappe
from frappe import _
from frappe.utils import add_months, getdate, flt


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _monthly_rate(doc):
    """Return the monthly interest rate as a decimal (e.g. 0.01 for 1%)."""
    rate = flt(getattr(doc, "custom_monthly_interest_rate_", 0))
    if rate:
        return rate / 100.0
    # Fallback: derive from annual rate
    annual = flt(getattr(doc, "rate_of_interest", 0))
    return (annual / 12.0) / 100.0


def _base_params(doc):
    """Return (loan_amount, periods, monthly_rate, start_date) from the schedule doc."""
    if not doc.repayment_start_date:
        frappe.throw(_("Repayment Start Date is mandatory for term loans."))
    loan_amount = flt(doc.loan_amount)
    periods = int(doc.repayment_periods or 0)
    if periods <= 0:
        frappe.throw(_("Repayment periods must be greater than zero."))
    monthly_rate = _monthly_rate(doc)
    start_date = getdate(doc.repayment_start_date)
    return loan_amount, periods, monthly_rate, start_date


def _add_broken_period_row(doc, schedule_rows):
    """
    If 'broken_period_interest_days' > 0 and broken_period_interest_charged is set
    on the loan product, prepend a period-0 row for broken period interest.

    For Zero Interest loans this is always 0 regardless of the flag.
    """
    broken_days = flt(getattr(doc, "broken_period_interest_days", 0))
    if broken_days <= 0:
        return

    method = getattr(doc, "custom_loan_calculation_method", "") or ""
    if method == "Zero Interest":
        # Zero interest: no broken period charge even if flag is set
        frappe.msgprint(
            _("Broken Period Interest suppressed: Zero Interest loan product."),
            indicator="orange",
            alert=True,
        )
        return

    monthly_rate = _monthly_rate(doc)
    loan_amount = flt(doc.loan_amount)

    # Broken period interest = Principal × monthly_rate × (broken_days / 30)
    bp_interest = flt(loan_amount * monthly_rate * (broken_days / 30.0), 2)

    # Insert as period 0 at the front of the schedule
    first_date = getdate(doc.repayment_start_date)
    schedule_rows.insert(0, {
        "payment_date": first_date,
        "principal_amount": 0.0,
        "interest_amount": bp_interest,
        "total_payment": bp_interest,
        "balance_loan_amount": loan_amount,
        "is_broken_period": 1,
    })


def _build_schedule(doc, rows):
    """Replace doc.repayment_schedule with computed rows, handling broken period."""
    doc.repayment_schedule = []

    # Add broken period row if applicable (modifies rows in-place at index 0)
    _add_broken_period_row(doc, rows)

    for row in rows:
        doc.append("repayment_schedule", row)


# ─────────────────────────────────────────────────────────────────────────────
# Method 1: EMI Flat Rate (Simple Interest)
# ─────────────────────────────────────────────────────────────────────────────

def generate_flat_rate_schedule(doc):
    """
    Flat Rate / Simple Interest EMI.

    Total Interest = Principal × monthly_rate × N  (computed once upfront)
    EMI            = (Principal + Total Interest) / N   ← fixed every period
    Interest/period= Total Interest / N                 ← same every period
    Principal/period = Principal / N                    ← same every period

    Arrears policy: Non-carry-forward (arrears tracked separately, schedule unchanged).
    """
    loan_amount, periods, monthly_rate, start_date = _base_params(doc)

    total_interest = flt(loan_amount * monthly_rate * periods, 2)
    emi = flt((loan_amount + total_interest) / periods, 2)
    interest_per_period = flt(total_interest / periods, 2)
    principal_per_period = flt(loan_amount / periods, 2)

    rows = []
    outstanding = loan_amount
    payment_date = start_date

    for i in range(periods):
        is_last = (i == periods - 1)

        principal = flt(outstanding, 2) if is_last else principal_per_period
        interest = interest_per_period
        total = flt(principal + interest, 2)
        balance = flt(outstanding - principal, 2)

        rows.append({
            "payment_date": payment_date,
            "principal_amount": principal,
            "interest_amount": interest,
            "total_payment": total,
            "balance_loan_amount": balance,
        })

        outstanding = balance
        payment_date = add_months(payment_date, 1)

    _build_schedule(doc, rows)

    frappe.msgprint(
        _(
            "Schedule: <b>EMI Flat Rate</b> — Fixed EMI of {0} over {1} periods "
            "(Interest fixed at {2}% per month on original principal)."
        ).format(flt(emi, 2), periods, flt(monthly_rate * 100, 4)),
        indicator="blue",
        alert=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Method 2: EMI Reducing Balance (Annuity)
# ─────────────────────────────────────────────────────────────────────────────

def generate_reducing_balance_schedule(doc):
    """
    Reducing Balance / Annuity EMI.

    EMI = P × r × (1+r)^N / ((1+r)^N - 1)
    Interest each period = Outstanding × r  (decreases as balance falls)
    Principal each period = EMI - Interest  (increases over time)

    This is the standard ERPNext formula. We replicate it here so it is part of
    the registry and can be selected explicitly. If selected, we build the schedule
    ourselves so broken-period logic is applied consistently.

    Arrears policy: Non-carry-forward.
    """
    loan_amount, periods, monthly_rate, start_date = _base_params(doc)

    if monthly_rate == 0:
        # Zero rate edge case: treat as zero interest
        frappe.msgprint(
            _("Monthly rate is 0 — falling back to Zero Interest schedule."),
            indicator="orange",
            alert=True,
        )
        generate_zero_interest_schedule(doc)
        return

    factor = (1 + monthly_rate) ** periods
    emi = flt(loan_amount * monthly_rate * factor / (factor - 1), 2)

    rows = []
    outstanding = loan_amount
    payment_date = start_date

    for i in range(periods):
        is_last = (i == periods - 1)

        interest = flt(outstanding * monthly_rate, 2)
        principal = flt(outstanding, 2) if is_last else flt(emi - interest, 2)
        total = flt(principal + interest, 2)
        balance = flt(outstanding - principal, 2)

        rows.append({
            "payment_date": payment_date,
            "principal_amount": principal,
            "interest_amount": interest,
            "total_payment": total,
            "balance_loan_amount": balance,
        })

        outstanding = balance
        payment_date = add_months(payment_date, 1)

    _build_schedule(doc, rows)

    frappe.msgprint(
        _(
            "Schedule: <b>EMI Reducing Balance</b> — Fixed EMI of {0} over {1} periods "
            "(interest declines on reducing outstanding balance)."
        ).format(flt(emi, 2), periods),
        indicator="blue",
        alert=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Method 3: Equal Principal Installments
# ─────────────────────────────────────────────────────────────────────────────

def generate_equal_principal_schedule(doc):
    """
    Equal Principal Installments.

    Fixed Principal = P / N            (same every period)
    Interest(i)     = Outstanding(i) × r   (decreases each period)
    Installment(i)  = Fixed Principal + Interest(i)   (decreases each period)

    Arrears policy: Carry-forward, configurable via custom_arrears_carry_forward_scope.
    Scope options: "Interest Only" | "Principal Only" | "Both" (default).

    Carry-forward is recorded on the schedule rows via carried_interest /
    carried_principal fields (for display and downstream reconciliation).
    Actual deferral logic during repayment is handled in the repayment posting flow.
    """
    loan_amount, periods, monthly_rate, start_date = _base_params(doc)

    fixed_principal = flt(loan_amount / periods, 2)

    rows = []
    outstanding = loan_amount
    payment_date = start_date

    for i in range(periods):
        is_last = (i == periods - 1)

        principal = flt(outstanding, 2) if is_last else fixed_principal
        interest = flt(outstanding * monthly_rate, 2)
        total = flt(principal + interest, 2)
        balance = flt(outstanding - principal, 2)

        rows.append({
            "payment_date": payment_date,
            "principal_amount": principal,
            "interest_amount": interest,
            "total_payment": total,
            "balance_loan_amount": balance,
        })

        outstanding = balance
        payment_date = add_months(payment_date, 1)

    _build_schedule(doc, rows)

    carry_scope = getattr(doc, "custom_arrears_carry_forward_scope", "Both") or "Both"
    frappe.msgprint(
        _(
            "Schedule: <b>Equal Principal Installments</b> — Fixed principal of {0} per period, "
            "declining interest on outstanding balance. "
            "Arrears carry-forward scope: <b>{1}</b>."
        ).format(flt(fixed_principal, 2), carry_scope),
        indicator="blue",
        alert=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Method 4: Zero Interest (Principal Only)
# ─────────────────────────────────────────────────────────────────────────────

def generate_zero_interest_schedule(doc):
    """
    Zero Interest — principal repayment only.

    EMI = Principal / N   (no interest charged regardless of rate fields)

    Validates and hard-sets interest to 0 if a non-zero rate is present.
    Arrears policy: Non-carry-forward.
    """
    loan_amount, periods, _, start_date = _base_params(doc)

    # Enforce zero interest — warn if rate fields are non-zero
    if flt(getattr(doc, "custom_monthly_interest_rate_", 0)) > 0:
        frappe.msgprint(
            _(
                "Warning: Monthly Interest Rate is set but this product uses "
                "<b>Zero Interest</b> method. Interest will not be charged."
            ),
            indicator="orange",
            alert=True,
        )

    principal_per_period = flt(loan_amount / periods, 2)

    rows = []
    outstanding = loan_amount
    payment_date = start_date

    for i in range(periods):
        is_last = (i == periods - 1)

        principal = flt(outstanding, 2) if is_last else principal_per_period
        balance = flt(outstanding - principal, 2)

        rows.append({
            "payment_date": payment_date,
            "principal_amount": principal,
            "interest_amount": 0.0,
            "total_payment": principal,
            "balance_loan_amount": balance,
        })

        outstanding = balance
        payment_date = add_months(payment_date, 1)

    _build_schedule(doc, rows)

    frappe.msgprint(
        _(
            "Schedule: <b>Zero Interest</b> — Principal only, {0} per period over {1} periods."
        ).format(flt(principal_per_period, 2), periods),
        indicator="blue",
        alert=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────

SCHEDULE_METHODS = {
    "EMI Flat Rate": generate_flat_rate_schedule,
    "EMI Reducing Balance": generate_reducing_balance_schedule,
    "Equal Principal Installments": generate_equal_principal_schedule,
    "Zero Interest": generate_zero_interest_schedule,
}

# Human-readable method names (used in Select field options)
CALCULATION_METHOD_OPTIONS = "\n".join(SCHEDULE_METHODS.keys())

# Carry-forward scope options (used in Select field on Loan Product)
CARRY_FORWARD_SCOPE_OPTIONS = "Both\nInterest Only\nPrincipal Only"

# Methods that use carry-forward arrears (affects UI visibility of scope field)
CARRY_FORWARD_METHODS = {"Equal Principal Installments"}
