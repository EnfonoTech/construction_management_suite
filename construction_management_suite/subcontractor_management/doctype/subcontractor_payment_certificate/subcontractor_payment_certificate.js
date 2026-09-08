frappe.ui.form.on("Subcontractor Payment Certificate", {
    refresh(frm) {
        frm.set_query("subcontract_agreement", () => ({ filters: { docstatus: 1 } }));
        CMS.linkButton(frm, __("Purchase Invoice"), "Purchase Invoice", frm.doc.purchase_invoice_ref);
    },

    subcontract_agreement(frm) {
        if (!frm.doc.subcontract_agreement) return;
        frappe.db.get_value("Subcontract Agreement", frm.doc.subcontract_agreement,
            ["project", "subcontractor", "company", "currency", "retention_percent", "total_certified"]
        ).then(r => {
            const a = r.message;
            if (!a) return;
            ["project", "subcontractor", "company", "currency"].forEach(f => CMS.fillIfBlank(frm, f, a[f]));
            CMS.fillIfBlank(frm, "retention_percent", a.retention_percent);
            CMS.fillIfBlank(frm, "previous_amount_certified", a.total_certified);
        });
    },
    certified_amount(frm) { CMS.recalc(frm); },
    retention_percent(frm) { CMS.recalc(frm); },
    advance_recovery(frm) { CMS.recalc(frm); },
    other_deductions(frm) { CMS.recalc(frm); },
    items_remove(frm) { CMS.recalc(frm); },

});

CMS.liveRows("Subcontractor Payment Item", ["amount_claimed"]);
