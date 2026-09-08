frappe.ui.form.on("Cost Code", {
    refresh(frm) {
        CMS.filterByCompany(frm, "parent_cost_code", { is_group: 1 });
        CMS.filterByCompany(frm, "debit_account", { is_group: 0, root_type: "Expense" });
        CMS.filterByCompany(frm, "credit_account", { is_group: 0 });
    },
    company(frm) { frm.trigger("refresh"); },
});
