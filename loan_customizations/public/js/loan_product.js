/*
 * Loan Product Form Client Script
 * =================================
 * Handles:
 *  1. Show/hide Edu Loans Repayment Slabs table based on calculation method
 *  2. Auto-set rate_of_interest to 0 for Edu Loans and Zero Interest
 *  3. Toggle interest field read-only for zero-interest methods
 */

frappe.ui.form.on("Loan Product", {
    refresh(frm) {
        _toggle_graduated_fields(frm);
    },

    custom_loan_calculation_method(frm) {
        _toggle_graduated_fields(frm);

        const method = frm.doc.custom_loan_calculation_method || "";

        // Auto-zero interest for graduated and zero-interest methods
        if (method === "Edu Loans" || method === "Zero Interest") {
            frm.set_value("rate_of_interest", 0);
            frm.set_value("custom_monthly_interest_rate_", 0);
        }
    },
});


function _toggle_graduated_fields(frm) {
    const method = (frm.doc.custom_loan_calculation_method || "").trim();
    const is_graduated = method === "Edu Loans";
    const is_zero_interest = method === "Zero Interest";
    const is_no_interest = is_graduated || is_zero_interest;

    // Show/hide the slab table
    frm.set_df_property(
        "custom_graduated_repayment_slabs",
        "hidden",
        is_graduated ? 0 : 1
    );

    // Make slab table mandatory when graduated is selected
    frm.set_df_property(
        "custom_graduated_repayment_slabs",
        "reqd",
        is_graduated ? 1 : 0
    );

    // Make interest fields read-only for zero-interest methods
    frm.set_df_property("rate_of_interest", "read_only", is_no_interest ? 1 : 0);
    frm.set_df_property("custom_monthly_interest_rate_", "read_only", is_no_interest ? 1 : 0);

    // Add a description note
    if (is_graduated) {
        frm.set_df_property(
            "rate_of_interest",
            "description",
            __("Automatically set to 0% for Edu Loans.")
        );
    } else if (is_zero_interest) {
        frm.set_df_property(
            "rate_of_interest",
            "description",
            __("Automatically set to 0% for Zero Interest loans.")
        );
    } else {
        frm.set_df_property("rate_of_interest", "description", "");
    }
}
