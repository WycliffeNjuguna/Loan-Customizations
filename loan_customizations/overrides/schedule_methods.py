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

RATE RESOLUTION (March 2026 fix):
  _monthly_rate() checks custom_monthly_interest_rate_ first, then falls
  back to rate_of_interest (annual) / 12. The override file's
  _sync_custom_fields_from_loan() ensures these fields are populated
  before we get here, even on server-side validate().
"""

import frappe
from frappe import _
from frappe.utils import add_months, getdate, flt


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _monthly_rate(doc):
    """
    Return the monthly interest rate as a decimal (e.g. 0.01 for 1%).

    Resolution order:
      1. custom_monthly_interest_rate_ (if > 0)  → divide by 100
      2. rate_of_interest (annual)               → divide by 12, then by 100

    Both fields should be populated by _sync_custom_fields_from_loan()
    in the override before this is called.
    """
    rate = flt(getattr(doc, "custom_monthly_interest_rate_", 0))
    if rate > 0:
        return rate / 100.0

    # Fallback: derive from annual rate
    annual = flt(getattr(doc, "rate_of_interest", 0))
    if annual > 0:
        return (annual / 12.0) / 100.0

    return 0.0


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
    if method in ("Zero Interest", "Graduated Repayment", "Edu Loans"):
        frappe.msgprint(
            _("Broken Period Interest suppressed: {0} loan product.").format(method),
            indicator="orange",
            alert=True,
        )
        return

    monthly_rate = _monthly_rate(doc)
    loan_amount = flt(doc.loan_amount)

    bp_interest = flt(loan_amount * monthly_rate * (broken_days / 30.0), 2)

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

    _add_broken_period_row(doc, rows)

    for row in rows:
        doc.append("repayment_schedule", row)


def _generate_zero_interest_core(doc):
    """
    Shared zero-interest schedule builder used by both the explicit
    Zero Interest method and as a fallback when rate resolves to 0.
    """
    loan_amount, periods, _, start_date = _base_params(doc)

    principal_per_period = flt(loan_amount / periods, 2)
    balance = loan_amount
    rows = []

    for i in range(periods):
        payment_date = add_months(start_date, i)

        if i == periods - 1:
            principal = flt(balance, 2)
        else:
            principal = principal_per_period

        balance = flt(balance - principal, 2)

        rows.append({
            "payment_date": payment_date,
            "principal_amount": principal,
            "interest_amount": 0.0,
            "total_payment": principal,
            "balance_loan_amount": balance,
        })

    _build_schedule(doc, rows)

    doc.monthly_repayment_amount = principal_per_period
    doc.total_interest_payable = 0.0
    doc.total_payment = loan_amount


# ─────────────────────────────────────────────────────────────────────────────
# Method 1: EMI Flat Rate (Simple Interest)
# ─────────────────────────────────────────────────────────────────────────────

def generate_flat_rate_schedule(doc):
    """
    Flat Rate / Simple Interest EMI.

    Total Interest = Principal × monthly_rate × N
    EMI = (Principal + Total Interest) / N   (constant every period)
    """
    loan_amount, periods, monthly_rate, start_date = _base_params(doc)

    if monthly_rate <= 0:
        frappe.msgprint(
            _("EMI Flat Rate: monthly rate is zero — generating zero-interest schedule."),
            indicator="orange", alert=True,
        )
        return _generate_zero_interest_core(doc)

    total_interest = flt(loan_amount * monthly_rate * periods, 2)
    interest_per_period = flt(total_interest / periods, 2)
    emi = flt((loan_amount + total_interest) / periods, 2)
    principal_per_period = flt(emi - interest_per_period, 2)

    balance = loan_amount
    rows = []

    for i in range(periods):
        payment_date = add_months(start_date, i)

        if i == periods - 1:
            principal = flt(balance, 2)
            interest = interest_per_period
            total = flt(principal + interest, 2)
        else:
            principal = principal_per_period
            interest = interest_per_period
            total = emi

        balance = flt(balance - principal, 2)

        rows.append({
            "payment_date": payment_date,
            "principal_amount": principal,
            "interest_amount": interest,
            "total_payment": total,
            "balance_loan_amount": balance,
        })

    _build_schedule(doc, rows)

    doc.monthly_repayment_amount = emi
    doc.total_interest_payable = total_interest
    doc.total_payment = flt(loan_amount + total_interest, 2)


# ─────────────────────────────────────────────────────────────────────────────
# Method 2: EMI Reducing Balance (Annuity / Amortising)
# ─────────────────────────────────────────────────────────────────────────────

def generate_emi_reducing_balance(doc):
    """
    Standard annuity formula:
      EMI = P × r × (1+r)^N / ((1+r)^N - 1)

    Interest each period = Outstanding Balance × monthly_rate
    Principal each period = EMI - Interest
    """
    loan_amount, periods, monthly_rate, start_date = _base_params(doc)

    if monthly_rate <= 0:
        frappe.msgprint(
            _("EMI Reducing Balance: monthly rate is zero — generating zero-interest schedule."),
            indicator="orange", alert=True,
        )
        return _generate_zero_interest_core(doc)

    r = monthly_rate
    n = periods
    emi = flt(loan_amount * r * ((1 + r) ** n) / (((1 + r) ** n) - 1), 2)

    balance = loan_amount
    total_interest = 0.0
    rows = []

    for i in range(periods):
        payment_date = add_months(start_date, i)
        interest = flt(balance * r, 2)

        if i == periods - 1:
            principal = flt(balance, 2)
            total = flt(principal + interest, 2)
        else:
            principal = flt(emi - interest, 2)
            total = emi

        balance = flt(balance - principal, 2)
        total_interest += interest

        rows.append({
            "payment_date": payment_date,
            "principal_amount": principal,
            "interest_amount": interest,
            "total_payment": total,
            "balance_loan_amount": max(balance, 0.0),
        })

    _build_schedule(doc, rows)

    doc.monthly_repayment_amount = emi
    doc.total_interest_payable = flt(total_interest, 2)
    doc.total_payment = flt(loan_amount + total_interest, 2)


# ─────────────────────────────────────────────────────────────────────────────
# Method 3: Equal Principal Installments
# ─────────────────────────────────────────────────────────────────────────────

def generate_equal_principal(doc):
    """
    Fixed principal per period. Interest on outstanding balance each period.
    Total installment declines over time.
    """
    loan_amount, periods, monthly_rate, start_date = _base_params(doc)

    if monthly_rate <= 0:
        frappe.msgprint(
            _("Equal Principal: monthly rate is zero — generating zero-interest schedule."),
            indicator="orange", alert=True,
        )
        return _generate_zero_interest_core(doc)

    principal_per_period = flt(loan_amount / periods, 2)
    balance = loan_amount
    total_interest = 0.0
    rows = []

    for i in range(periods):
        payment_date = add_months(start_date, i)
        interest = flt(balance * monthly_rate, 2)

        if i == periods - 1:
            principal = flt(balance, 2)
        else:
            principal = principal_per_period

        total = flt(principal + interest, 2)
        balance = flt(balance - principal, 2)
        total_interest += interest

        rows.append({
            "payment_date": payment_date,
            "principal_amount": principal,
            "interest_amount": interest,
            "total_payment": total,
            "balance_loan_amount": max(balance, 0.0),
        })

    _build_schedule(doc, rows)

    doc.monthly_repayment_amount = flt(principal_per_period + (loan_amount * monthly_rate), 2)
    doc.total_interest_payable = flt(total_interest, 2)
    doc.total_payment = flt(loan_amount + total_interest, 2)


# ─────────────────────────────────────────────────────────────────────────────
# Method 4: Zero Interest
# ─────────────────────────────────────────────────────────────────────────────

def generate_zero_interest(doc):
    """
    Principal-only schedule. No interest regardless of rate fields.
    """
    frappe.msgprint(
        _("Zero Interest loan product — interest is suppressed even if rate fields are set."),
        indicator="blue",
        alert=True,
    )
    _generate_zero_interest_core(doc)


# ─────────────────────────────────────────────────────────────────────────────
# Method 5: Graduated Repayment (Edu Loans)
# ─────────────────────────────────────────────────────────────────────────────

def _get_graduated_slabs(loan_product_name):
    """
    Fetch the graduated repayment slabs from the Loan Product's child table.
    Returns a list of dicts sorted by balance_from descending (highest slab first).

    Each slab: { balance_from, balance_to, monthly_deduction }
    """
    if not loan_product_name:
        return []

    slabs = frappe.get_all(
        "Graduated Repayment Slab",
        filters={"parent": loan_product_name, "parenttype": "Loan Product"},
        fields=["balance_from", "balance_to", "monthly_deduction"],
        order_by="balance_from desc",
    )

    return slabs


def _get_slab_deduction(amount, slabs):
    """
    Given an amount (typically the original loan amount), find the matching
    slab and return the monthly deduction amount.

    Slabs are checked from highest to lowest. The first slab where
    balance_from <= amount <= balance_to is used.

    If no slab matches (shouldn't happen with proper configuration),
    returns the deduction from the lowest slab as fallback.
    """
    for slab in slabs:
        if flt(slab.balance_from) <= flt(amount) <= flt(slab.balance_to):
            return flt(slab.monthly_deduction)

    # Fallback: if amount is above all slabs, use the highest slab
    if slabs and flt(amount) > flt(slabs[0].balance_to):
        return flt(slabs[0].monthly_deduction)

    # Fallback: if amount is below all slabs, use the lowest slab
    if slabs:
        return flt(slabs[-1].monthly_deduction)

    return 0.0


def generate_graduated_repayment(doc):
    """
    Graduated Repayment for education loans (slab-based).

    How it works:
      1. Reads the graduated repayment slabs from the Loan Product
      2. Looks up the ORIGINAL LOAN AMOUNT in the slab table to determine
         a fixed monthly deduction for the entire repayment period
      3. Applies the same deduction every period until the loan is paid off
      4. The last period pays off whatever remains

    This is a ZERO-INTEREST method — no interest is charged.
    The number of periods is determined dynamically by the slab deduction
    and loan amount, NOT by the repayment_periods field. repayment_periods
    is updated after generation to reflect the actual number of periods
    produced.

    Example for KES 30,000 loan with slabs:
      25,001-30,000 → 2,000/month
      20,001-25,000 → 1,750/month
      15,001-20,000 → 1,500/month
      ...
    A KES 30,000 loan falls in the 25,001-30,000 slab, so the borrower
    pays 2,000/month for 15 months (14 × 2,000 + 1 × 2,000 remainder).
    The deduction does NOT step down as the balance decreases.
    """
    if not doc.repayment_start_date:
        frappe.throw(_("Repayment Start Date is mandatory for term loans."))

    loan_amount = flt(doc.loan_amount)
    if loan_amount <= 0:
        frappe.throw(_("Loan Amount must be greater than zero."))

    start_date = getdate(doc.repayment_start_date)

    # Get the loan product name — it might be on the schedule doc or we
    # need to look it up from the linked Loan
    loan_product = getattr(doc, "loan_product", "") or ""
    if not loan_product and getattr(doc, "loan", ""):
        loan_product = frappe.db.get_value("Loan", doc.loan, "loan_product") or ""

    if not loan_product:
        frappe.throw(
            _("Cannot generate graduated repayment schedule: no Loan Product found. "
              "Please ensure the Loan has a Loan Product set.")
        )

    # Fetch slabs
    slabs = _get_graduated_slabs(loan_product)

    if not slabs:
        frappe.throw(
            _("Loan Product <b>{0}</b> has no Graduated Repayment Slabs defined. "
              "Please add slabs in the Loan Product before creating this loan.").format(loan_product)
        )

    # Determine the fixed monthly deduction based on the ORIGINAL loan amount
    deduction = _get_slab_deduction(loan_amount, slabs)

    if deduction <= 0:
        frappe.throw(
            _("Graduated Repayment: slab returned zero deduction for loan amount {0}. "
              "Check slab configuration on Loan Product {1}.").format(
                frappe.format_value(loan_amount, {"fieldtype": "Currency"}),
                loan_product,
            )
        )

    # Build the schedule with a fixed deduction every period
    balance = loan_amount
    rows = []
    max_periods = 120  # Safety limit: 10 years max

    period = 0
    while balance > 0 and period < max_periods:
        payment_date = add_months(start_date, period)

        # Last period: pay off the remaining balance
        if balance <= deduction:
            principal = flt(balance, 2)
        else:
            principal = flt(deduction, 2)

        balance = flt(balance - principal, 2)

        rows.append({
            "payment_date": payment_date,
            "principal_amount": principal,
            "interest_amount": 0.0,
            "total_payment": principal,
            "balance_loan_amount": max(balance, 0.0),
        })

        period += 1

    if balance > 0:
        frappe.msgprint(
            _("Warning: graduated repayment schedule hit the {0}-period safety limit "
              "with {1} still outstanding. Check slab configuration.").format(
                max_periods,
                frappe.format_value(balance, {"fieldtype": "Currency"}),
            ),
            indicator="red",
            alert=True,
        )

    _build_schedule(doc, rows)

    # Update summary fields
    actual_periods = len(rows)
    first_deduction = rows[0]["total_payment"] if rows else 0

    doc.repayment_periods = actual_periods
    doc.monthly_repayment_amount = first_deduction
    doc.total_interest_payable = 0.0
    doc.total_payment = loan_amount

    frappe.msgprint(
        _("Graduated Repayment schedule generated: {0} periods from slabs on <b>{1}</b>. "
          "Fixed monthly deduction: {2} (based on loan amount slab). "
          "Final payment: {3}.").format(
            actual_periods,
            loan_product,
            frappe.format_value(deduction, {"fieldtype": "Currency"}),
            frappe.format_value(rows[-1]["total_payment"] if rows else 0, {"fieldtype": "Currency"}),
        ),
        indicator="green",
        alert=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────

SCHEDULE_METHODS = {
    "EMI Flat Rate":                generate_flat_rate_schedule,
    "EMI Reducing Balance":         generate_emi_reducing_balance,
    "Equal Principal Installments": generate_equal_principal,
    "Zero Interest":                generate_zero_interest,
    "Graduated Repayment":          generate_graduated_repayment,
    "Edu Loans":                    generate_graduated_repayment,
}
