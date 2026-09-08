frappe.ui.form.on("Material Forecast", {
    refresh(frm) {
        frm.set_query("project", () => ({ filters: { status: ["!=", "Completed"] } }));
        CMS.filterByProject(frm, "boq_ref", { docstatus: 1 });

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

    project(frm) { CMS.fillFromProject(frm, { company: "company" }); },
    items_remove: recalc,
});

frappe.ui.form.on("Material Forecast Item", {
    boq_qty: row_calc,
    waste_factor: row_calc,
    estimated_rate: row_calc,
    already_ordered_qty: row_calc,
});

function row_calc(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    const net = flt(row.boq_qty) * (1 + flt(row.waste_factor) / 100);
    const to_order = Math.max(0, net - flt(row.already_ordered_qty));
    frappe.model.set_value(cdt, cdn, "net_qty_required", net);
    frappe.model.set_value(cdt, cdn, "qty_to_order", to_order);
    frappe.model.set_value(cdt, cdn, "estimated_value", to_order * flt(row.estimated_rate));
    recalc(frm);
}

function recalc(frm) {
    frm.set_value("total_forecast_qty_value", CMS.sum(frm.doc.items, "estimated_value"));
}
