frappe.ui.form.on("Cost Estimation", {
    refresh(frm) {
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

    contingency_percent: recalc,
    selling_price: recalc,
    items_remove: recalc,
});

frappe.ui.form.on("Cost Estimation Item", {
    qty: row_calc,
    material_cost: row_calc,
    labour_cost: row_calc,
    equipment_cost: row_calc,
    overhead_cost: row_calc,
});

function row_calc(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    // Rows linked to a Rate Analysis are priced on the server; leave those alone.
    if (!row.rate_analysis_ref) {
        const unit = flt(row.material_cost) + flt(row.labour_cost)
            + flt(row.equipment_cost) + flt(row.overhead_cost);
        frappe.model.set_value(cdt, cdn, "unit_cost", unit);
    }
    frappe.model.set_value(cdt, cdn, "total_cost", flt(row.qty) * flt(row.unit_cost));
    recalc(frm);
}

function recalc(frm) {
    const rows = frm.doc.items || [];
    const subtotal = CMS.sum(rows, "total_cost");
    const contingency = subtotal * flt(frm.doc.contingency_percent) / 100;
    const total = subtotal + contingency;
    frm.set_value("estimated_material_cost", rows.reduce((t, r) => t + flt(r.material_cost) * flt(r.qty), 0));
    frm.set_value("estimated_labour_cost", rows.reduce((t, r) => t + flt(r.labour_cost) * flt(r.qty), 0));
    frm.set_value("estimated_equipment_cost", rows.reduce((t, r) => t + flt(r.equipment_cost) * flt(r.qty), 0));
    frm.set_value("estimated_overhead_cost", rows.reduce((t, r) => t + flt(r.overhead_cost) * flt(r.qty), 0));
    frm.set_value("contingency_amount", contingency);
    frm.set_value("total_estimated_cost", total);
    if (flt(frm.doc.selling_price) > 0) {
        frm.set_value("margin_percent", (flt(frm.doc.selling_price) - total) / flt(frm.doc.selling_price) * 100);
    }
}
