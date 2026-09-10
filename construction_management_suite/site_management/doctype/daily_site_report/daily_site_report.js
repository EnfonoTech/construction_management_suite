frappe.ui.form.on("Daily Site Report", {
    refresh(frm) {
        CMS.uomQuery(frm, "materials_used", "item_code");
        CMS.filterProjects(frm);
        if (!frm.doc.report_date) frm.set_value("report_date", frappe.datetime.get_today());

        if (frm.doc.docstatus === 1 && (frm.doc.materials_used || []).length) {
            frm.add_custom_button(__("Material Consumption"), () => {
                frappe.new_doc("Material Consumption Entry", {
                    project: frm.doc.project, company: frm.doc.company,
                    posting_date: frm.doc.report_date,
                    daily_site_report_ref: frm.doc.name,
                });
            }, __("Create"));
        }
    },
    company(frm) {
        CMS.filterProjects(frm);
        CMS.clearForeignProject(frm);
    },

    project(frm) { CMS.fillFromProject(frm, { company: "company" }); },
    labour_remove(frm) { CMS.recalc(frm); },
    equipment_remove(frm) { CMS.recalc(frm); },

});



function plant_row(frm, cdt, cdn) {
    CMS.rowAmount(cdt, cdn, "hours_worked", "hourly_rate", "cost");
}

/** Show today's labour and plant spend in the dashboard, before saving. */

CMS.liveRows("Site Report Labour", ["headcount", "daily_rate", "overtime_hours", "overtime_rate"]);
CMS.liveRows("Site Report Equipment", ["hours_worked", "idle_hours", "hourly_rate"]);
