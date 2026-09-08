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

    retention_percent: recalc,
    advance_recovery_amount: recalc,
    other_deductions: recalc,
    previous_cumulative_amount: recalc,
    items_remove: recalc,
});

frappe.ui.form.on("IPC Item", {
    contract_qty: row_calc,
    contract_rate: row_calc,
    previous_qty_claimed: row_calc,
    qty_this_period: row_calc,
});

function row_calc(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    const cum = flt(row.previous_qty_claimed) + flt(row.qty_this_period);
    frappe.model.set_value(cdt, cdn, "contract_amount", flt(row.contract_qty) * flt(row.contract_rate));
    frappe.model.set_value(cdt, cdn, "cumulative_qty", cum);
    frappe.model.set_value(cdt, cdn, "amount_this_period", flt(row.qty_this_period) * flt(row.contract_rate));
    frappe.model.set_value(cdt, cdn, "cumulative_amount", cum * flt(row.contract_rate));
    const contract_amount = flt(row.contract_qty) * flt(row.contract_rate);
    frappe.model.set_value(cdt, cdn, "percent_complete",
        contract_amount > 0 ? (cum * flt(row.contract_rate)) / contract_amount * 100 : 0);
    recalc(frm);
}

/** Mirrors the server calculation so the net payable is visible before saving. */
function recalc(frm) {
    const gross = CMS.sum(frm.doc.items, "amount_this_period");
    const retention = gross * flt(frm.doc.retention_percent) / 100;
    frm.set_value("gross_amount_this_period", gross);
    frm.set_value("cumulative_amount_to_date", flt(frm.doc.previous_cumulative_amount) + gross);
    frm.set_value("retention_amount", retention);
    frm.set_value("net_payable_this_period",
        gross - retention - flt(frm.doc.advance_recovery_amount) - flt(frm.doc.other_deductions));
}

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
