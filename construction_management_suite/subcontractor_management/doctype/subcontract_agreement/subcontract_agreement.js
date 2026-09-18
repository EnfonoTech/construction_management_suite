frappe.ui.form.on("Subcontract Agreement", {
    onload(frm) {
        CMS.defaultTaxTemplate(frm, "purchase_taxes_template");
    },

    taxes_and_charges(frm) { CMS.loadTaxTemplate(frm); },

    taxes_remove(frm) { CMS.recalc(frm); },

    refresh(frm) {
        CMS.uomQuery(frm, "items", "item_code");
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
    subcontract_value(frm) { CMS.recalc(frm); },
    advance_percent(frm) { CMS.recalc(frm); },
    items_remove(frm) { CMS.recalc(frm); },

});



/** Offer the line total as the contract value while the agreement is still a draft. */

CMS.liveRows("Subcontract Item", ["qty", "rate"]);

frappe.ui.form.on("Subcontract Item", {
    item_code(frm, cdt, cdn) {
        CMS.fetchItemRate(frm, cdt, cdn, { itemfield: "item_code", target: "rate" });
    },
});

CMS.liveRows("Purchase Taxes and Charges", ["charge_type", "rate", "tax_amount", "row_id"]);
