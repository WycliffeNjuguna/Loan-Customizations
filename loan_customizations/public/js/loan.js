/**
 * Loan Form Client Script — loan_customizations
 * ================================================
 * Handles:
 *  1. Field visibility based on custom_loan_calculation_method
 *  2. For Graduated Repayment / Edu Loans:
 *     - Auto-calculates repayment_periods from slabs when loan_amount changes
 *     - Makes repayment_periods editable (not hidden) so user can save
 *     - Shows a preview of the graduated schedule
 *  3. For other methods: standard field behaviour
 */

frappe.ui.form.on("Loan", {

    refresh: function (frm) {
        _apply_method_field_rules(frm);
    },

    loan_product: function (frm) {
        // When loan product changes, re-apply field rules after fetch_from runs
        setTimeout(function () {
            _apply_method_field_rules(frm);
            _auto_calc_graduated_periods(frm);
        }, 500);
    },

    custom_loan_calculation_method: function (frm) {
        _apply_method_field_rules(frm);
    },

    loan_amount: function (frm) {
        _auto_calc_graduated_periods(frm);
    },

    repayment_method: function (frm) {
        _apply_method_field_rules(frm);
    },
});


/**
 * Apply field visibility/mandatory rules based on the calculation method.
 *
 * For Graduated Repayment / Edu Loans:
 *   - repayment_periods: visible, NOT mandatory (auto-calculated from slabs)
 *   - repayment_method: auto-set to "Repay Over Number of Periods"
 *   - monthly_repayment_amount: read-only (determined by slabs)
 *   - rate fields: hidden (zero interest)
 *
 * For other methods:
 *   - Standard lending app behaviour (no overrides)
 */
function _apply_method_field_rules(frm) {
    var method = (frm.doc.custom_loan_calculation_method || "").trim();
    var is_graduated = (method === "Graduated Repayment" || method === "Edu Loans");

    if (is_graduated) {
        // Ensure repayment_method is set so the standard lending JS
        // doesn't hide the periods field
        if (frm.doc.repayment_method !== "Repay Over Number of Periods") {
            frm.set_value("repayment_method", "Repay Over Number of Periods");
        }

        // Make sure repayment_periods is visible and editable
        frm.toggle_display("repayment_periods", true);
        frm.toggle_reqd("repayment_periods", false);
        frm.set_df_property("repayment_periods", "read_only", 0);
        frm.set_df_property("repayment_periods", "description",
            "Auto-calculated from graduated slabs. Will be updated when the schedule is generated.");

        // Monthly repayment is determined by slabs, not user input
        frm.set_df_property("monthly_repayment_amount", "read_only", 1);
        frm.set_df_property("monthly_repayment_amount", "description",
            "Determined by graduated repayment slabs — varies each period.");

        // Hide rate fields — Edu loans are zero interest
        frm.set_df_property("custom_monthly_interest_rate_", "hidden", 1);

    } else {
        // Reset to defaults for non-graduated methods
        frm.set_df_property("repayment_periods", "description", "");
        frm.set_df_property("monthly_repayment_amount", "read_only", 0);
        frm.set_df_property("monthly_repayment_amount", "description", "");
        frm.set_df_property("custom_monthly_interest_rate_", "hidden", 0);
    }
}


/**
 * For Graduated Repayment: estimate the number of periods by walking
 * the slabs on the client side. This gives the user a preview and
 * ensures repayment_periods is populated before save.
 *
 * The server-side generator in schedule_methods.py does the authoritative
 * calculation — this is just a UX convenience.
 */
function _auto_calc_graduated_periods(frm) {
    var method = (frm.doc.custom_loan_calculation_method || "").trim();
    if (method !== "Graduated Repayment" && method !== "Edu Loans") return;

    var loan_amount = flt(frm.doc.loan_amount);
    var loan_product = frm.doc.loan_product;
    if (!loan_amount || !loan_product) return;

    // Fetch slabs from the Loan Product
    frappe.call({
        method: "frappe.client.get",
        args: {
            doctype: "Loan Product",
            name: loan_product,
        },
        callback: function (r) {
            if (!r.message) return;

            var slabs = (r.message.custom_graduated_repayment_slabs || []).sort(function (a, b) {
                return flt(b.balance_from) - flt(a.balance_from);
            });

            if (!slabs.length) {
                frappe.show_alert({
                    message: __("No graduated repayment slabs found on {0}", [loan_product]),
                    indicator: "orange"
                }, 5);
                return;
            }

            // Walk the slabs to estimate periods
            var balance = loan_amount;
            var periods = 0;
            var max_periods = 120;
            var schedule_preview = [];

            while (balance > 0 && periods < max_periods) {
                var deduction = _find_slab_deduction(balance, slabs);
                if (deduction <= 0) break;

                var payment = Math.min(deduction, balance);
                balance = flt(balance - payment, 2);
                periods++;
                schedule_preview.push({
                    period: periods,
                    payment: payment,
                    balance: balance
                });
            }

            if (periods > 0) {
                frm.set_value("repayment_periods", periods);
                frm.set_value("monthly_repayment_amount", schedule_preview[0].payment);
                frm.set_value("total_payment", loan_amount);
                frm.set_value("total_interest_payable", 0);

                frappe.show_alert({
                    message: __("Graduated schedule: {0} periods. First payment: {1}, Last: {2}", [
                        periods,
                        format_currency(schedule_preview[0].payment),
                        format_currency(schedule_preview[schedule_preview.length - 1].payment)
                    ]),
                    indicator: "green"
                }, 7);
            }
        }
    });
}


/**
 * Find the matching slab deduction for a given balance.
 * Mirrors the server-side _get_slab_deduction() logic.
 */
function _find_slab_deduction(balance, slabs) {
    for (var i = 0; i < slabs.length; i++) {
        if (flt(slabs[i].balance_from) <= balance && balance <= flt(slabs[i].balance_to)) {
            return flt(slabs[i].monthly_deduction);
        }
    }
    // Fallback: above all slabs → highest slab
    if (slabs.length && balance > flt(slabs[0].balance_to)) {
        return flt(slabs[0].monthly_deduction);
    }
    // Fallback: below all slabs → lowest slab
    if (slabs.length) {
        return flt(slabs[slabs.length - 1].monthly_deduction);
    }
    return 0;
}