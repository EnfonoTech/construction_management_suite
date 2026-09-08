frappe.ui.form.on("Daily Site Report", {
    refresh(frm) {
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
    labour_remove: labour_total,
    equipment_remove: labour_total,
});

frappe.ui.form.on("Site Report Labour", {
    headcount: labour_row,
    daily_rate: labour_row,
});

frappe.ui.form.on("Site Report Equipment", {
    hours_worked: plant_row,
    hourly_rate: plant_row,
});

function labour_row(frm, cdt, cdn) {
    CMS.rowAmount(cdt, cdn, "headcount", "daily_rate", "daily_cost");
    labour_total(frm);
}
function plant_row(frm, cdt, cdn) {
    CMS.rowAmount(cdt, cdn, "hours_worked", "hourly_rate", "cost");
}

/** Show today's labour and plant spend in the dashboard, before saving. */
function labour_total(frm) {
    const labour = CMS.sum(frm.doc.labour, "daily_cost");
    const plant = CMS.sum(frm.doc.equipment, "cost");
    const heads = CMS.sum(frm.doc.labour, "headcount");
    if (!labour && !plant) return;
    frm.dashboard.clear_headline();
    frm.dashboard.set_headline(
        __("{0} on site &nbsp;·&nbsp; labour {1} &nbsp;·&nbsp; plant {2}", [
            heads,
            format_currency(labour, frappe.defaults.get_default("currency")),
            format_currency(plant, frappe.defaults.get_default("currency")),
        ])
    );
}
