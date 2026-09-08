frappe.ui.form.on("Project Budget", {
    refresh(frm) {
        CMS.filterProjects(frm);
        CMS.filterByCompany(frm, "wip_account", { is_group: 0 });
        frm.set_query("cost_code", "items", () => ({ filters: { company: frm.doc.company, is_group: 0 } }));

        // refresh_actuals is whitelisted but had no way to call it from the desk.
        if (!frm.is_new()) {
            frm.add_custom_button(__("Refresh Actuals"), () => {
                frm.call("refresh_actuals").then(() => frm.reload_doc());
            });
        }
    },

    project(frm) { CMS.fillFromProject(frm, { company: "company" }); },
    company(frm) {
        CMS.filterProjects(frm);
        CMS.clearForeignProject(frm);
        if (frm.doc.company) CMS.currencyFromCompany(frm, frm.doc.company);
    },
    items_remove: recalc,
});

frappe.ui.form.on("Project Budget Item", {
    budgeted_amount: row_calc,
    actual_amount: row_calc,
});

function row_calc(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    frappe.model.set_value(cdt, cdn, "variance", flt(row.budgeted_amount) - flt(row.actual_amount));
    recalc(frm);
}

function recalc(frm) {
    const budget = CMS.sum(frm.doc.items, "budgeted_amount");
    if (budget && !flt(frm.doc.total_budget)) frm.set_value("total_budget", budget);
    frm.set_value("variance_amount", flt(frm.doc.total_budget) - flt(frm.doc.total_actual_cost));
    if (flt(frm.doc.total_budget) > 0) {
        frm.set_value("budget_utilization_percent",
            flt(frm.doc.total_actual_cost) / flt(frm.doc.total_budget) * 100);
    }
}
