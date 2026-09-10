frappe.ui.form.on("Subcontract Agreement", {
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
