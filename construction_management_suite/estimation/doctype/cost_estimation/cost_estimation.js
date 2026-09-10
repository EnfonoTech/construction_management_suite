frappe.ui.form.on("Cost Estimation", {
    refresh(frm) {
        CMS.uomQuery(frm, "items", "item_code");
        if (!frm.is_new()) frm.add_custom_button(__("Rate Build-up"), () => CMS.showRateBuildUp(frm), __("View"));
        CMS.filterByProject(frm, "boq_ref", { docstatus: 1 });
        CMS.filterProjects(frm);

        if (frm.doc.docstatus === 1 && frm.doc.project) {
            frappe.db.get_value("Project Budget",
                { project: frm.doc.project, docstatus: ["<", 2] }, "name"
            ).then(r => {
                if (r.message && r.message.name) {
                    CMS.linkButton(frm, __("Project Budget"), "Project Budget", r.message.name);
                }
            });
        }
    },

    project(frm) {
        CMS.fillFromProject(frm, { company: "company" });
    },

    company(frm) {
        CMS.filterProjects(frm);
        CMS.clearForeignProject(frm);
        if (frm.doc.company) CMS.currencyFromCompany(frm, frm.doc.company);
    },
    contingency_percent(frm) { CMS.recalc(frm); },
    selling_price(frm) { CMS.recalc(frm); },
    items_remove(frm) { CMS.recalc(frm); },

});

CMS.liveRows("Cost Estimation Item", ["qty", "material_cost", "labour_cost", "equipment_cost", "overhead_cost", "unit_cost"]);
