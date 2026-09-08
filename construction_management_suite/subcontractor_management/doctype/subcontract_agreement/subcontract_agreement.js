frappe.ui.form.on("Subcontract Agreement", {
    refresh(frm) {
        CMS.filterProjects(frm);
        CMS.linkButton(frm, __("Purchase Order"), "Purchase Order", frm.doc.purchase_order_ref);

        if (frm.doc.docstatus === 1 && frm.doc.status !== "Terminated") {
            frm.add_custom_button(__("Payment Certificate"), () => {
                frappe.new_doc("Subcontractor Payment Certificate", {
                    subcontract_agreement: frm.doc.name,
                });
            }, __("Create"));
            frm.add_custom_button(__("Work Order"), () => {
                frappe.new_doc("Subcontractor Work Order", {
                    subcontract_agreement: frm.doc.name,
                });
            }, __("Create"));
        }
    },

    project(frm) {
        CMS.fillFromProject(frm, { company: "company", end_date: "expected_end_date" });
    },

    company(frm) {
        CMS.filterProjects(frm);
        CMS.clearForeignProject(frm);
        if (frm.doc.company) CMS.currencyFromCompany(frm, frm.doc.company);
    },

    subcontract_value: advance,
    advance_percent: advance,
    items_remove: totals,
});

frappe.ui.form.on("Subcontract Item", {
    qty: (frm, cdt, cdn) => { CMS.rowAmount(cdt, cdn, "qty", "rate", "amount"); totals(frm); },
    rate: (frm, cdt, cdn) => { CMS.rowAmount(cdt, cdn, "qty", "rate", "amount"); totals(frm); },
});

function advance(frm) {
    frm.set_value("advance_amount", flt(frm.doc.subcontract_value) * flt(frm.doc.advance_percent) / 100);
}

/** Offer the line total as the contract value while the agreement is still a draft. */
function totals(frm) {
    if (frm.doc.docstatus !== 0) return;
    const lines = CMS.sum(frm.doc.items, "amount");
    if (lines && !flt(frm.doc.subcontract_value)) frm.set_value("subcontract_value", lines);
}
