frappe.ui.form.on("Material Consumption Entry", {
    refresh(frm) {
        frm.set_query("project", () => ({ filters: { status: ["!=", "Completed"] } }));
        CMS.filterByCompany(frm, "warehouse", { is_group: 0 });
        CMS.filterByProject(frm, "daily_site_report_ref", { docstatus: 1 });
        if (!frm.doc.posting_date) frm.set_value("posting_date", frappe.datetime.get_today());
        CMS.linkButton(frm, __("Stock Entry"), "Stock Entry", frm.doc.stock_entry_ref);
    },
    project(frm) { CMS.fillFromProject(frm, { company: "company" }); },
    company(frm) { CMS.filterByCompany(frm, "warehouse", { is_group: 0 }); },
    items_remove: total,
});

frappe.ui.form.on("Material Consumption Item", {
    qty: (frm, cdt, cdn) => { CMS.rowAmount(cdt, cdn, "qty", "valuation_rate", "amount"); total(frm); },
    valuation_rate: (frm, cdt, cdn) => { CMS.rowAmount(cdt, cdn, "qty", "valuation_rate", "amount"); total(frm); },
});

function total(frm) {
    const t = CMS.sum(frm.doc.items, "amount");
    if (!t) return;
    frm.dashboard.clear_headline();
    frm.dashboard.set_headline(
        __("Consuming {0}", [format_currency(t, frappe.defaults.get_default("currency"))]));
}
