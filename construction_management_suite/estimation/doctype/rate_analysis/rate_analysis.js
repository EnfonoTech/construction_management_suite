frappe.ui.form.on("Rate Analysis", {
    refresh(frm) {
        CMS.uomQuery(frm, "resources", "resource_item");
        if (!frm.is_new() && frm.doc.status === "Approved") {
            frm.add_custom_button(__("Apply to BOQ Items"), () => apply_to_boq(frm), __("Actions"));
        }
    },
    qty(frm, cdt, cdn) { calc_resource(frm, cdt, cdn); },
    rate(frm, cdt, cdn) { calc_resource(frm, cdt, cdn); },
    waste_factor(frm, cdt, cdn) { calc_resource(frm, cdt, cdn); },
    output_qty(frm) { CMS.recalc(frm); },
    resources_remove(frm) { CMS.recalc(frm); },

});

function calc_resource(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    row.amount = flt(row.qty) * flt(row.rate);
    row.net_amount = flt(row.amount) * (1 + flt(row.waste_factor) / 100);
    frappe.model.set_value(cdt, cdn, "amount", row.amount);
    frappe.model.set_value(cdt, cdn, "net_amount", row.net_amount);
    calc_totals(frm);
}

function calc_totals(frm) {
    const totals = { Material: 0, Labour: 0, Equipment: 0, Subcontract: 0, Overhead: 0 };
    (frm.doc.resources || []).forEach(r => {
        if (totals[r.resource_type] !== undefined) totals[r.resource_type] += flt(r.net_amount);
    });
    frm.set_value("total_material_cost", totals.Material);
    frm.set_value("total_labour_cost", totals.Labour);
    frm.set_value("total_equipment_cost", totals.Equipment);
    frm.set_value("total_subcontract_cost", totals.Subcontract);
    frm.set_value("total_overhead_cost", totals.Overhead);
    const total = Object.values(totals).reduce((a, b) => a + b, 0);
    frm.set_value("total_cost", total);
    const output = flt(frm.doc.output_qty) || 1;
    frm.set_value("rate_per_unit", total / output);
}

function apply_to_boq(frm) {
    if (!frm.doc.item_code) {
        frappe.msgprint({
            title: __("Set an Item first"),
            message: __("This analysis has no Item, so there is no way to tell which BOQ lines it prices. Set <b>Item</b> on this Rate Analysis and save."),
            indicator: "orange",
        });
        return;
    }
    // The item comes from the analysis itself — asking the user to retype it was
    // the old behaviour and just invited typos.
    frappe.prompt(
        [{
            fieldname: "boq", label: __("BOQ"), fieldtype: "Link", options: "BOQ", reqd: 1,
            get_query: () => ({ filters: { docstatus: 0 } }),
            description: __("Draft BOQs only. Every line for {0} will be priced.", [frm.doc.item_code]),
        }],
        (vals) => {
            frappe.call({
                method: "construction_management_suite.api.boq.apply_rate_analysis_to_boq",
                args: { rate_analysis: frm.doc.name, boq: vals.boq, item_code: frm.doc.item_code },
                freeze: true,
                callback: r => frappe.msgprint(r.message),
            });
        },
        __("Apply to BOQ"),
        __("Apply")
    );
}

CMS.liveRows("Rate Analysis Resource", ["resource_type", "qty", "rate", "waste_factor"]);
