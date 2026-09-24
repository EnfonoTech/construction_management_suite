frappe.ui.form.on("Site Material Request", {
    refresh(frm) {
        CMS.uomQuery(frm, "items", "item_code");
        CMS.filterProjects(frm);
        CMS.filterByCompany(frm, "warehouse", { is_group: 0 });
        if (!frm.doc.request_date) frm.set_value("request_date", frappe.datetime.get_today());
        CMS.linkButton(frm, __("Material Request"), "Material Request", frm.doc.material_request_ref);

        if (frm.doc.docstatus === 0 && frm.doc.project) {
            frm.add_custom_button(__("Get from the Take-off"), () => {
                frm.call("get_items_from_take_off").then(r => {
                    frm.refresh_field("items");
                    frappe.show_alert(r.message
                        ? __("{0} line(s) added", [r.message])
                        : __("Nothing outstanding — the take-off is covered"));
                });
            });
        }
        if (frm.doc.docstatus === 1) {
            frm.add_custom_button(__("Site Transfer"), () => {
                frappe.new_doc("Site Transfer", {
                    to_project: frm.doc.project, company: frm.doc.company,
                });
            }, __("Create"));
        }
    },
    project(frm) { CMS.fillFromProject(frm, { company: "company" }); },
    company(frm) {
        CMS.filterByCompany(frm, "warehouse", { is_group: 0 });
        CMS.filterProjects(frm);
        CMS.clearForeignProject(frm);
    },
});

frappe.ui.form.on("Site Material Request Item", {
    items_add(frm, cdt, cdn) {
        const row = locals[cdt][cdn];
        if (row.warehouse) return;
        // What the rest of the request says first — a request split across two
        // stores is answering a question the project's default cannot.
        const first = frm.doc.items.length > 1 ? frm.doc.items[0].warehouse : null;
        if (first) {
            frappe.model.set_value(cdt, cdn, "warehouse", first);
            return;
        }
        CMS.projectStore(frm).then(store => {
            if (store && !row.warehouse) frappe.model.set_value(cdt, cdn, "warehouse", store);
        });
    },
});
