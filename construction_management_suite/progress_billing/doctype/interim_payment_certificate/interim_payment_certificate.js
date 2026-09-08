frappe.ui.form.on("Interim Payment Certificate", {
    refresh(frm) {
        CMS.filterByProject(frm, "boq_ref", { docstatus: 1 });
        CMS.filterProjects(frm);
        CMS.linkButton(frm, __("Sales Invoice"), "Sales Invoice", frm.doc.sales_invoice_ref);

        if (frm.doc.docstatus === 1) {
            frm.add_custom_button(__("Next Certificate"), () => {
                frappe.new_doc("Interim Payment Certificate", {
                    project: frm.doc.project, boq_ref: frm.doc.boq_ref,
                    company: frm.doc.company, currency: frm.doc.currency,
                    client: frm.doc.client, contract_value: frm.doc.contract_value,
                    retention_percent: frm.doc.retention_percent,
                });
            }, __("Create"));

            frm.add_custom_button(__("Retention Release"), () => {
                frappe.new_doc("Retention Release", {
                    project: frm.doc.project, client: frm.doc.client,
                    company: frm.doc.company, currency: frm.doc.currency,
                });
            }, __("Create"));
        }
    },

    project(frm) {
        CMS.fillFromProject(frm, {
            company: "company",
            client: "customer",
            contract_value: "cms_contract_value",
            retention_percent: "cms_retention_percent",
        }).then(() => frm.doc.project && fetch_previous_position(frm));
    },

    company(frm) {
        CMS.filterProjects(frm);
        CMS.clearForeignProject(frm);
        if (frm.doc.company) CMS.currencyFromCompany(frm, frm.doc.company);
    },
    retention_percent(frm) { CMS.recalc(frm); },
    advance_recovery_amount(frm) { CMS.recalc(frm); },
    other_deductions(frm) { CMS.recalc(frm); },
    previous_cumulative_amount(frm) { CMS.recalc(frm); },
    items_remove(frm) { CMS.recalc(frm); },

});



/** Mirrors the server calculation so the net payable is visible before saving. */

/** Pull forward where the last certificate on this project left off. */
function fetch_previous_position(frm) {
    if (frm.doc.previous_cumulative_amount) return;
    frappe.call({
        method: "construction_management_suite.api.boq.get_previous_ipc_position",
        args: { project: frm.doc.project },
        callback: (r) => {
            if (!r.message) return;
            if (r.message.cumulative_amount) {
                frm.set_value("previous_cumulative_amount", r.message.cumulative_amount);
            }
            if (!frm.doc.ipc_number && r.message.next_ipc_number) {
                frm.set_value("ipc_number", r.message.next_ipc_number);
            }
        },
    });
}

CMS.liveRows("IPC Item", ["contract_qty", "contract_rate", "previous_qty_claimed", "qty_this_period"]);
