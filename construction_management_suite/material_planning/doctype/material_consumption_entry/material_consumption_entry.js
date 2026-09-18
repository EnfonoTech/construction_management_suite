frappe.ui.form.on("Material Consumption Entry", {
    refresh(frm) {
        CMS.uomQuery(frm, "items", "item_code");
        CMS.filterProjects(frm);
        CMS.filterByCompany(frm, "warehouse", { is_group: 0 });
        CMS.filterByProject(frm, "daily_site_report_ref", { docstatus: 1 });
        if (!frm.doc.posting_date) frm.set_value("posting_date", frappe.datetime.get_today());
        CMS.linkButton(frm, __("Stock Entry"), "Stock Entry", frm.doc.stock_entry_ref);
        show_against_takeoff(frm);

        if (frm.doc.docstatus === 0 && frm.doc.project) {
            frm.add_custom_button(__("Get Planned Materials"), () => {
                frm.call("get_items_from_forecast").then(r => {
                    frm.refresh_field("items");
                    CMS.recalc(frm);
                    frappe.show_alert(r.message
                        ? { message: __("{0} material(s) added — enter the quantities issued",
                                        [r.message]), indicator: "green" }
                        : { message: __("Nothing outstanding against the take-off for this project"),
                            indicator: "orange" });
                });
            });
        }
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

frappe.ui.form.on("Material Consumption Item", {
    item_code(frm, cdt, cdn) {
        CMS.fetchItemRate(frm, cdt, cdn, { itemfield: "item_code", target: "valuation_rate", valuation: true, warehouse: "warehouse" });
    },
});


/** Consumed against what the bills were priced to use. */
function show_against_takeoff(frm) {
    if (frm.is_new() || !frm.doc.project) return;
    frappe.call({
        method: "construction_management_suite.api.boq.get_consumption_position",
        args: { project: frm.doc.project },
        callback: (r) => {
            const d = r.message;
            if (!d || !d.allowed) return;
            const pct = Math.min(d.used / d.allowed * 100, 150);
            const colour = pct > 100 ? "progress-bar-danger"
                : pct >= 75 ? "progress-bar-orange" : "progress-bar-blue";
            if (frm.dashboard.progress_area) frm.dashboard.progress_area.body.empty();
            frm.dashboard.add_progress(
                __("{0} of {1} take-off value consumed", [
                    format_currency(d.used_value, frm.doc.currency),
                    format_currency(d.allowed_value, frm.doc.currency)]),
                [{ title: `${pct.toFixed(0)}%`, width: `${Math.min(pct, 100)}%`,
                   progress_class: colour }]
            );
        },
    });
}
