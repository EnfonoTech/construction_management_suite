frappe.ui.form.on("Site Material Request", {
    refresh(frm) {
        frm.set_query("project", () => ({ filters: { status: ["!=", "Completed"] } }));
        CMS.filterByCompany(frm, "warehouse", { is_group: 0 });
        if (!frm.doc.request_date) frm.set_value("request_date", frappe.datetime.get_today());
        CMS.linkButton(frm, __("Material Request"), "Material Request", frm.doc.material_request_ref);
    },
    project(frm) { CMS.fillFromProject(frm, { company: "company" }); },
    company(frm) { CMS.filterByCompany(frm, "warehouse", { is_group: 0 }); },
});

frappe.ui.form.on("Site Material Request Item", {
    items_add(frm, cdt, cdn) {
        const row = locals[cdt][cdn];
        if (!row.warehouse && frm.doc.items.length > 1) {
            frappe.model.set_value(cdt, cdn, "warehouse", frm.doc.items[0].warehouse);
        }
    },
});
