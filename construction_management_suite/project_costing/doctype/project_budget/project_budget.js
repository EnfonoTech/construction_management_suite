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
            frm.add_custom_button(__("Cost Variance"), () => {
                frappe.set_route("query-report", "Project Cost Variance", { project: frm.doc.project });
            }, __("View"));
        }
    },

    project(frm) { CMS.fillFromProject(frm, { company: "company" }); },
    company(frm) {
        CMS.filterProjects(frm);
        CMS.clearForeignProject(frm);
        if (frm.doc.company) CMS.currencyFromCompany(frm, frm.doc.company);
    },
    total_budget(frm) { CMS.recalc(frm); },
    total_actual_cost(frm) { CMS.recalc(frm); },
    items_remove(frm) { CMS.recalc(frm); },

});

CMS.liveRows("Project Budget Item", ["budgeted_amount", "actual_amount"]);
