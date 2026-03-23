/*
 * Loan Form Client Script
 * ========================
 * Handles:
 *  1. Repayment Method either/or visibility toggle
 *     - "Repay Fixed Amount per Period" → show amount, hide periods
 *     - "Repay Over Number of Periods" → show periods, hide amount
 *  2. Edu Loans auto-handling
 *     - Hides periods field (computed dynamically by schedule generator)
 */

frappe.ui.form.on("Loan", {
    refresh(frm) {
        _toggle_repayment_fields(frm);
    },

    repayment_method(frm) {
        _toggle_repayment_fields(frm);
    },

    custom_loan_calculation_method(frm) {
        _toggle_repayment_fields(frm);

        // Force zero interest for graduated and zero-interest methods
        const method = frm.doc.custom_loan_calculation_method || "";
        if (method === "Edu Loans" || method === "Zero Interest") {
            frm.set_value("rate_of_interest", 0);
            frm.set_value("custom_monthly_interest_rate_", 0);
        }
    },

    loan_product(frm) {
        // When loan product changes, fetch the custom fields
        if (frm.doc.loan_product) {
            frappe.db.get_value(
                "Loan Product",
                frm.doc.loan_product,
                [
                    "custom_loan_calculation_method",
                    "custom_monthly_interest_rate_",
                    "custom_arrears_policy",
                    "custom_arrears_carry_forward_scope",
                ],
                (r) => {
                    if (r) {
                        frm.set_value("custom_loan_calculation_method", r.custom_loan_calculation_method || "");
                        frm.set_value("custom_monthly_interest_rate_", r.custom_monthly_interest_rate_ || 0);
                        frm.set_value("custom_arrears_policy", r.custom_arrears_policy || "");
                        frm.set_value("custom_arrears_carry_forward_scope", r.custom_arrears_carry_forward_scope || "");
                        _toggle_repayment_fields(frm);
                    }
                }
            );
        }
    },
});


function _toggle_repayment_fields(frm) {
    const method = (frm.doc.repayment_method || "").trim();
    const calc_method = (frm.doc.custom_loan_calculation_method || "").trim();
    const is_graduated = calc_method === "Edu Loans";

    if (method === "Repay Fixed Amount per Period") {
        // Show amount, hide periods
        frm.set_df_property("monthly_repayment_amount", "hidden", 0);
        frm.set_df_property("monthly_repayment_amount", "reqd", 1);
        frm.set_df_property("repayment_periods", "hidden", 1);
        frm.set_df_property("repayment_periods", "reqd", 0);

    } else if (method === "Repay Over Number of Periods") {
        // Show periods, hide amount
        // Exception: Edu Loans computes periods dynamically
        frm.set_df_property("monthly_repayment_amount", "hidden", 1);
        frm.set_df_property("monthly_repayment_amount", "reqd", 0);

        if (is_graduated) {
            // Graduated computes periods dynamically — hide both
            frm.set_df_property("repayment_periods", "hidden", 1);
            frm.set_df_property("repayment_periods", "reqd", 0);
        } else {
            frm.set_df_property("repayment_periods", "hidden", 0);
            frm.set_df_property("repayment_periods", "reqd", 1);
        }

    } else {
        // No method selected — show both, require neither
        frm.set_df_property("monthly_repayment_amount", "hidden", 0);
        frm.set_df_property("monthly_repayment_amount", "reqd", 0);
        frm.set_df_property("repayment_periods", "hidden", 0);
        frm.set_df_property("repayment_periods", "reqd", 0);
    }

    // For graduated repayment, show a note about dynamic periods
    if (is_graduated && method === "Repay Over Number of Periods") {
        frm.set_df_property(
            "repayment_periods",
            "description",
            __("Auto-calculated from graduated slab table. Leave blank.")
        );
    } else {
        frm.set_df_property("repayment_periods", "description", "");
    }
}
