frappe.ui.form.on("Subcontractor Work Order", {
    refresh(frm) {
        frm.set_query("subcontract_agreement", () => ({ filters: { docstatus: 1 } }));
    },

    subcontract_agreement(frm) {
        if (!frm.doc.subcontract_agreement) return;
        frappe.db.get_value("Subcontract Agreement", frm.doc.subcontract_agreement,
            ["project", "subcontractor", "company", "currency", "start_date", "end_date"]
        ).then(r => {
            const a = r.message;
            if (!a) return;
            ["project", "subcontractor", "company", "currency", "start_date", "end_date"]
                .forEach(f => CMS.fillIfBlank(frm, f, a[f]));
        });
    },

    items_remove: recalc,
});

frappe.ui.form.on("Subcontractor Work Order Item", {
    contract_qty: row_calc,
    contract_rate: row_calc,
    completed_qty: row_calc,
});

function row_calc(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    frappe.model.set_value(cdt, cdn, "contract_amount", flt(row.contract_qty) * flt(row.contract_rate));
    frappe.model.set_value(cdt, cdn, "completed_amount", flt(row.completed_qty) * flt(row.contract_rate));
    frappe.model.set_value(cdt, cdn, "completion_percent",
        flt(row.contract_qty) > 0 ? flt(row.completed_qty) / flt(row.contract_qty) * 100 : 0);
    recalc(frm);
}

function recalc(frm) {
    const contract = CMS.sum(frm.doc.items, "contract_amount");
    const done = CMS.sum(frm.doc.items, "completed_amount");
    frm.set_value("total_contract_value", contract);
    frm.set_value("total_completed_value", done);
    frm.set_value("completion_percent", contract > 0 ? done / contract * 100 : 0);
}
