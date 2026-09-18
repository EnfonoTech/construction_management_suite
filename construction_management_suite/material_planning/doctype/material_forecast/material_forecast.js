frappe.ui.form.on("Material Forecast", {
    refresh(frm) {
        CMS.uomQuery(frm, "items", "item_code");
        CMS.filterProjects(frm);
        CMS.filterByProject(frm, "boq_ref", { docstatus: 1 });
        CMS.linkButton(frm, __("BOQ"), "BOQ", frm.doc.boq_ref);
        CMS.linkButton(frm, __("Material Request"), "Material Request", frm.doc.material_request_ref);
        show_position(frm);

        if (frm.doc.docstatus === 0 && frm.doc.project) {
            frm.add_custom_button(__("Get Items from BOQ"), () => {
                frm.call("get_items_from_boq").then(r => {
                    frm.refresh_field("items");
                    CMS.recalc(frm);
                    const m = r.message || {};
                    const parts = [];
                    if (m.added) parts.push(__("{0} material(s) added", [m.added]));
                    if (m.updated) parts.push(__("{0} refreshed", [m.updated]));
                    if (m.already_planned) {
                        parts.push(__("{0} already covered by another forecast",
                                      [m.already_planned]));
                    }
                    frappe.show_alert(parts.length
                        ? { message: parts.join(", "), indicator: m.added || m.updated ? "green" : "orange" }
                        : { message: __("No priced line on this project explodes into materials — link a Rate Analysis whose resources name Items"),
                            indicator: "orange" });
                });
            });
        }
        if (frm.doc.docstatus === 1) {
            frm.add_custom_button(__("Site Material Request"), () => {
                frappe.new_doc("Site Material Request", {
                    project: frm.doc.project, company: frm.doc.company,
                });
            }, __("Create"));
        }

        // This endpoint existed with no way to reach it from the desk.
        if (frm.doc.docstatus === 1) {
            frm.add_custom_button(__("Material Request"), () => {
                frappe.call({
                    method: "construction_management_suite.api.boq.create_material_request_from_forecast",
                    args: { forecast_name: frm.doc.name },
                    freeze: true,
                    freeze_message: __("Creating Material Request…"),
                    callback: (r) => {
                        if (r.message) frappe.set_route("Form", "Material Request", r.message);
                    },
                });
            }, __("Create"));
        }
    },

    company(frm) {
        CMS.filterProjects(frm);
        CMS.clearForeignProject(frm);
    },

    project(frm) { CMS.fillFromProject(frm, { company: "company" }); },
    items_remove(frm) { CMS.recalc(frm); },

});

CMS.liveRows("Material Forecast Item", ["boq_qty", "waste_factor", "already_ordered_qty", "estimated_rate"]);

frappe.ui.form.on("Material Forecast Item", {
    item_code(frm, cdt, cdn) {
        CMS.fetchItemRate(frm, cdt, cdn, { itemfield: "item_code", target: "estimated_rate" });
    },
});


/** Forecast against what has actually been bought and used. */
function show_position(frm) {
    if (frm.is_new() || !(frm.doc.items || []).length) return;
    const required = frm.doc.items.reduce((t, r) => t + flt(r.net_qty_required), 0);
    const ordered = frm.doc.items.reduce((t, r) => t + flt(r.already_ordered_qty), 0);
    if (!required) return;
    const pct = Math.min(ordered / required * 100, 100);
    if (frm.dashboard.progress_area) frm.dashboard.progress_area.body.empty();
    frm.dashboard.add_progress(
        __("{0} of {1} forecast quantity already on order", [
            format_number(ordered, null, 0), format_number(required, null, 0)]),
        [{ title: `${pct.toFixed(0)}%`, width: `${pct}%`,
           progress_class: pct >= 100 ? "progress-bar-green" : "progress-bar-blue" }]
    );
}
