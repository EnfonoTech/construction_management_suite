frappe.ui.form.on("Site Transfer", {
    refresh(frm) {
        CMS.uomQuery(frm, "items", "item_code");
        CMS.filterByCompany(frm, "from_warehouse", { is_group: 0 });
        CMS.filterByCompany(frm, "to_warehouse", { is_group: 0 });
        CMS.filterProjects(frm, "from_project");
        CMS.filterProjects(frm, "to_project");
        if (!frm.doc.transfer_date) frm.set_value("transfer_date", frappe.datetime.get_today());
        CMS.linkButton(frm, __("Stock Entry"), "Stock Entry", frm.doc.stock_entry_ref);
    },
    to_project(frm) { CMS.fillFromProject(frm, { company: "company" }, "to_project"); },
    from_project(frm) { CMS.fillFromProject(frm, { company: "company" }, "from_project"); },
    company(frm) {
        CMS.filterByCompany(frm, "from_warehouse", { is_group: 0 });
        CMS.filterByCompany(frm, "to_warehouse", { is_group: 0 });
        CMS.filterProjects(frm, "from_project");
        CMS.filterProjects(frm, "to_project");
        CMS.clearForeignProject(frm, "from_project");
        CMS.clearForeignProject(frm, "to_project");
    },
    to_warehouse(frm) {
        if (frm.doc.from_warehouse && frm.doc.from_warehouse === frm.doc.to_warehouse) {
            frappe.msgprint(__("Source and destination warehouses must be different"));
            frm.set_value("to_warehouse", null);
        }
    },
});
