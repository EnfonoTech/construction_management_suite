frappe.ui.form.on("Material Forecast", {
    refresh(frm) {
        CMS.uomQuery(frm, "items", "item_code");
        CMS.filterProjects(frm);
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

    company(frm) {
        CMS.filterProjects(frm);
        CMS.clearForeignProject(frm);
    },

    project(frm) { CMS.fillFromProject(frm, { company: "company" }); },
    items_remove(frm) { CMS.recalc(frm); },

});

CMS.liveRows("Material Forecast Item", ["boq_qty", "waste_factor", "already_ordered_qty", "estimated_rate"]);
