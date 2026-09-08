frappe.ui.form.on("Material Consumption Entry", {
    refresh(frm) {
        CMS.filterProjects(frm);
        CMS.filterByCompany(frm, "warehouse", { is_group: 0 });
        CMS.filterByProject(frm, "daily_site_report_ref", { docstatus: 1 });
        if (!frm.doc.posting_date) frm.set_value("posting_date", frappe.datetime.get_today());
        CMS.linkButton(frm, __("Stock Entry"), "Stock Entry", frm.doc.stock_entry_ref);
    },
    project(frm) { CMS.fillFromProject(frm, { company: "company" }); },
    company(frm) {
        CMS.filterByCompany(frm, "warehouse", { is_group: 0 });
        CMS.filterProjects(frm);
        CMS.clearForeignProject(frm);
    },
    items_remove(frm) { CMS.recalc(frm); },

});

CMS.liveRows("Material Consumption Item", ["qty", "valuation_rate"]);
