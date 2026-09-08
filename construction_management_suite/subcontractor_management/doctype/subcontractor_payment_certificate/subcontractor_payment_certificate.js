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

    certified_amount: recalc,
    retention_percent: recalc,
    advance_recovery: recalc,
    other_deductions: recalc,
    items_remove: recalc,
});

frappe.ui.form.on("Subcontractor Payment Item", {
    amount_claimed: recalc,
});

function recalc(frm) {
    const claimed = CMS.sum(frm.doc.items, "amount_claimed");
    const retention = flt(frm.doc.certified_amount) * flt(frm.doc.retention_percent) / 100;
    frm.set_value("gross_amount_claimed", claimed);
    frm.set_value("retention_deduction", retention);
    frm.set_value("net_payable", flt(frm.doc.certified_amount) - retention
        - flt(frm.doc.advance_recovery) - flt(frm.doc.other_deductions));
}
