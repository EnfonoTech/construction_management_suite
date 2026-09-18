frappe.ui.form.on("Interim Payment Certificate", {
    refresh(frm) {
        CMS.filterByProject(frm, "boq_ref", { docstatus: 1 });
        CMS.filterProjects(frm);
        CMS.linkButton(frm, __("Sales Invoice"), "Sales Invoice", frm.doc.sales_invoice_ref);
        show_progress(frm);

        if (frm.doc.docstatus === 0 && frm.doc.boq_ref) {
            frm.add_custom_button(__("Get Items from BOQ"), () => get_items(frm));
        }

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
        }).then(() => {
            if (!frm.doc.project) return;
            fetch_previous_position(frm);
            // A project that states no retention falls back to the module
            // default rather than to zero.
            if (!frm.doc.retention_percent) {
                frappe.db.get_single_value("Construction Settings", "default_retention_percent")
                    .then(v => v && frm.set_value("retention_percent", v));
            }
        });
    },

    company(frm) {
        CMS.filterProjects(frm);
        CMS.clearForeignProject(frm);
        if (frm.doc.company) CMS.currencyFromCompany(frm, frm.doc.company);
    },
    boq_ref(frm) {
        if (frm.doc.boq_ref && !(frm.doc.items || []).length) get_items(frm);
    },

    taxes_and_charges(frm) {
        if (!frm.doc.taxes_and_charges) return;
        // ERPNext's own loader, so a template behaves here exactly as on an invoice.
        frappe.call({
            method: "erpnext.controllers.accounts_controller.get_taxes_and_charges",
            args: {
                master_doctype: "Sales Taxes and Charges Template",
                master_name: frm.doc.taxes_and_charges,
            },
            callback: (r) => {
                if (!r.message) return;
                frm.clear_table("taxes");
                r.message.forEach(row => frm.add_child("taxes", row));
                frm.refresh_field("taxes");
                CMS.recalc(frm);
            },
        });
    },

    retention_percent(frm) { CMS.recalc(frm); },
    advance_recovery_amount(frm) { CMS.recalc(frm); },
    other_deductions(frm) { CMS.recalc(frm); },
    previous_cumulative_amount(frm) { CMS.recalc(frm); },
    items_remove(frm) { CMS.recalc(frm); },
    taxes_remove(frm) { CMS.recalc(frm); },

});



/** Pull in every BOQ line that still has work left to certify. */
function get_items(frm) {
    frm.call("get_items_from_boq").then(r => {
        const added = (r && r.message) || 0;
        frm.refresh_field("items");
        CMS.recalc(frm);
        frappe.show_alert(
            added
                ? { message: __("{0} line(s) added", [added]), indicator: "green" }
                : { message: __("Nothing left to certify on this BOQ"), indicator: "orange" }
        );
    });
}

/** Where this certificate leaves the contract, above the form. */
function show_progress(frm) {
    const p = frm.doc.__onload && frm.doc.__onload.progress;
    if (!p || !p.contract_value) return;

    const pct = Math.min(Math.max(p.percent_complete, 0), 100);
    const colour = pct >= 100 ? "green" : pct >= 50 ? "blue" : "orange";
    const fmt = v => format_currency(v, frm.doc.currency);

    // add_progress APPENDS a chart every call and refresh fires more than once,
    // so the bars stacked up. Empty the area first.
    if (frm.dashboard.progress_area) frm.dashboard.progress_area.body.empty();
    // The bar carries the figures; a paragraph under it would only repeat them.
    frm.dashboard.add_progress(
        __("{0} of {1} certified", [fmt(p.cumulative_amount), fmt(p.contract_value)]),
        [{ title: `${pct.toFixed(1)}%`, width: `${pct}%`, progress_class: `progress-bar-${colour}` }]
    );
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

// previous_qty_claimed is read-only now — the server recomputes it from the
// certificates already submitted, so it is not a field anyone types into.
CMS.liveRows("IPC Item", ["contract_qty", "contract_rate", "qty_this_period"]);
CMS.liveRows("Sales Taxes and Charges", ["charge_type", "rate", "tax_amount", "row_id"]);
