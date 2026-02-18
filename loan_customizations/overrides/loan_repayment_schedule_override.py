import frappe
from frappe import _
from frappe.utils import add_months, getdate, flt
from lending.loan_management.doctype.loan_repayment_schedule.loan_repayment_schedule import LoanRepaymentSchedule


class CustomLoanRepaymentSchedule(LoanRepaymentSchedule):
    """
    Custom override for ERPNext Lending — Equal Principal + Monthly Rate schedule.

    TRIGGER CONDITIONS (both must be true):
        1. repayment_method == "Repay Over Number of Periods"
        2. custom_monthly_interest_rate_ field is set and > 0

    FORMULA:
        Fixed Principal  = Loan Amount / N                          (same every month)
        Interest(i)      = Outstanding Balance(i) × monthly_rate%  (declines each month)
        EMI(i)           = Fixed Principal + Interest(i)            (declines each month)

    FALLBACK:
        If custom_monthly_interest_rate_ is blank or zero, the standard ERPNext
        annual-rate amortisation logic runs completely unchanged.

    CUSTOM FIELD REQUIRED:
        Add 'custom_monthly_interest_rate_' (Float) to:
          - Loan Product  (user sets it here)
          - Loan          (fetch_from: loan_product.custom_monthly_interest_rate_)
          - Loan Repayment Schedule  (fetch_from: loan.custom_monthly_interest_rate_)

        See custom_fields/setup_custom_fields.py to create these automatically.
    """

    def make_repayment_schedule(self):
        if self._use_custom_schedule():
            self._make_equal_principal_schedule()
        else:
            super().make_repayment_schedule()

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _use_custom_schedule(self):
        """Returns True only when BOTH conditions are met."""
        return (
            self.repayment_method == "Repay Over Number of Periods"
            and self.repayment_periods
            and self.repayment_periods > 0
            and flt(getattr(self, "custom_monthly_interest_rate_", 0)) > 0
        )

    def _make_equal_principal_schedule(self):
        """
        Builds a fixed-principal, declining-interest repayment schedule.

        Each row:
            principal_amount    = fixed (Loan Amount / N)
            interest_amount     = outstanding × monthly_rate / 100
            total_payment       = principal + interest   ← decreases each month
            balance_loan_amount = outstanding after principal deduction
        """
        if not self.repayment_start_date:
            frappe.throw(_("Repayment Start Date is mandatory for term loans"))

        self.repayment_schedule = []

        loan_amount     = flt(self.loan_amount)
        periods         = int(self.repayment_periods)
        monthly_rate    = flt(self.custom_monthly_interest_rate_) / 100.0

        fixed_principal = flt(loan_amount / periods, 2)
        outstanding     = loan_amount
        payment_date    = getdate(self.repayment_start_date)

        for i in range(periods):
            # Last instalment absorbs any rounding residual so balance hits exactly 0
            if i == periods - 1:
                principal_amount = flt(outstanding, 2)
            else:
                principal_amount = fixed_principal

            interest_amount = flt(outstanding * monthly_rate, 2)
            total_payment   = flt(principal_amount + interest_amount, 2)
            balance_after   = flt(outstanding - principal_amount, 2)

            self.append("repayment_schedule", {
                "payment_date":        payment_date,
                "principal_amount":    principal_amount,
                "interest_amount":     interest_amount,
                "total_payment":       total_payment,
                "balance_loan_amount": balance_after,
            })

            outstanding  = balance_after
            payment_date = add_months(payment_date, 1)

        frappe.msgprint(
            _(
                "Schedule: <b>Equal Principal + Monthly Rate ({0}%)</b>. "
                "EMI reduces each period as interest declines on the outstanding balance."
            ).format(flt(self.custom_monthly_interest_rate_, 4)),
            indicator="blue",
            alert=True,
        )