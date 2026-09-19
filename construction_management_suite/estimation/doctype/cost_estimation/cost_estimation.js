frappe.ui.form.on("Cost Estimation", {
    refresh(frm) {
        CMS.uomQuery(frm, "items", "item_code");
        CMS.rateAnalysisQuery(frm, "items");
        if (!frm.is_new()) frm.add_custom_button(__("Rate Build-up"), () => CMS.showRateBuildUp(frm), __("View"));
        CMS.filterByProject(frm, "boq_ref", { docstatus: 1 });
        CMS.filterProjects(frm);

        // The analysis stays editable after approval so an unpriced line can
        // still reach the take-off; the scope itself must not move.
        if (frm.doc.docstatus === 1 && frm.fields_dict.items) {
            frm.fields_dict.items.grid.cannot_add_rows = true;
            frm.fields_dict.items.grid.cannot_delete_rows = true;
            // Editable only while this estimate says so, so the grid matches
            // what the server will accept.
            frm.fields_dict.items.grid.toggle_enable(
                "rate_analysis_ref", Boolean(frm.doc.allow_pricing_after_approval)
            );
        }

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

    allow_pricing_after_approval(frm) { frm.refresh(); },

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
