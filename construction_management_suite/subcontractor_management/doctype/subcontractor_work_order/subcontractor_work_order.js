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
    items_remove(frm) { CMS.recalc(frm); },

});

CMS.liveRows("Subcontractor Work Order Item", ["contract_qty", "contract_rate", "completed_qty"]);
