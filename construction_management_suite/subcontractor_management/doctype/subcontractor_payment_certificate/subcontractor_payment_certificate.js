frappe.ui.form.on("Subcontractor Payment Certificate", {
    onload(frm) {
        CMS.defaultTaxTemplate(frm);
    },

    taxes_and_charges(frm) { CMS.loadTaxTemplate(frm); },

    taxes_remove(frm) { CMS.recalc(frm); },

    refresh(frm) {
        frm.set_query("subcontract_agreement", () => ({
            filters: Object.assign({ docstatus: 1 },
                frm.doc.project ? { project: frm.doc.project } : {}),
        }));
        CMS.linkButton(frm, __("Purchase Invoice"), "Purchase Invoice", frm.doc.purchase_invoice_ref);
        CMS.linkButton(frm, __("Agreement"), "Subcontract Agreement", frm.doc.subcontract_agreement);
        show_position(frm);
        CMS.filterByCompany(frm, "warehouse", { is_group: 0 });

        if (frm.doc.docstatus === 0 && frm.doc.subcontract_agreement) {
            // Plenty of subcontracts never get a work order: the scope is
            // agreed, the trade does it, and the certificate is raised against
            // the agreement. That path had no button, so the lines were typed
            // by hand or a work order was invented for the sake of one.
            frm.add_custom_button(__("Get from the Agreement"), () => {
                frm.call("get_from_agreement").then(r => {
                    frm.refresh_field("items");
                    CMS.recalc(frm);
                    frappe.show_alert(r.message
                        ? { message: __("{0} line(s) claimed from the agreement", [r.message]),
                            indicator: "green" }
                        : { message: __("Every line on this agreement is certified in full"),
                            indicator: "orange" });
                });
            }, __("Get"));

            frm.add_custom_button(__("Get Completed Work"), () => {
                frm.call("get_completed_from_work_orders").then(r => {
                    frm.refresh_field("items");
                    CMS.recalc(frm);
                    frappe.show_alert(r.message
                        ? { message: __("{0} line(s) claimed from the work orders", [r.message]),
                            indicator: "green" }
                        : { message: __("Nothing built and unclaimed on this agreement"),
                            indicator: "orange" });
                });
            }, __("Get"));
        }
        if (frm.doc.docstatus === 1) {
            frm.add_custom_button(__("Next Certificate"), () => {
                frappe.new_doc("Subcontractor Payment Certificate", {
                    subcontract_agreement: frm.doc.subcontract_agreement,
                    project: frm.doc.project, subcontractor: frm.doc.subcontractor,
                    retention_percent: frm.doc.retention_percent,
                });
            }, __("Create"));
        }
    },

    subcontract_agreement(frm) {
        if (!frm.doc.subcontract_agreement) return;
        frappe.db.get_value("Subcontract Agreement", frm.doc.subcontract_agreement,
            ["project", "subcontractor", "company", "currency", "retention_percent",
             "subcontract_value", "total_certified", "advance_amount"]
        ).then(r => {
            const a = r.message;
            if (!a) return;
            ["project", "subcontractor", "company", "currency"].forEach(f => CMS.fillIfBlank(frm, f, a[f]));
            // The company arrives with the agreement, and the default template
            // is the company's — so it can only be asked for once that lands.
            CMS.defaultTaxTemplate(frm);
            CMS.fillIfBlank(frm, "retention_percent", a.retention_percent);

        });
        CMS.revealActions(frm);
    },
    certified_amount(frm) { CMS.recalc(frm); },
    retention_percent(frm) { CMS.recalc(frm); },
    advance_recovery(frm) { CMS.recalc(frm); },
    other_deductions(frm) { CMS.recalc(frm); },
    items_remove(frm) { CMS.recalc(frm); },

});

// The quantity and the rate drive the amount, so the grid has to react to all
// three: watching the amount alone meant typing a quantity showed nothing.
CMS.liveRows("Subcontractor Payment Item", ["qty_completed", "contract_rate", "amount_claimed"]);

frappe.ui.form.on("Subcontractor Payment Item", {
    qty_completed(frm, cdt, cdn) { price(frm, cdt, cdn); },
    contract_rate(frm, cdt, cdn) { price(frm, cdt, cdn); },
});

function price(frm, cdt, cdn) {
    const row = CMS.row(cdt, cdn);
    if (flt(row.qty_completed) && flt(row.contract_rate)) {
        frappe.model.set_value(cdt, cdn, "amount_claimed",
                               flt(row.qty_completed) * flt(row.contract_rate));
    }
}

CMS.liveRows("Purchase Taxes and Charges", ["charge_type", "rate", "tax_amount", "row_id"]);


/** Where this trade stands against its agreement. */
function show_position(frm) {
    if (frm.is_new() || !frm.doc.subcontract_agreement) return;
    frappe.db.get_value("Subcontract Agreement", frm.doc.subcontract_agreement,
        ["subcontract_value", "total_certified", "total_paid", "advance_amount"]
    ).then(r => {
        const a = r.message;
        if (!a || !flt(a.subcontract_value)) return;
        const certified = flt(a.total_certified);
        const pct = Math.min(certified / flt(a.subcontract_value) * 100, 100);
        const colour = pct >= 100 ? "green" : pct >= 50 ? "blue" : "orange";
        const fmt = v => format_currency(v, frm.doc.currency);
        if (frm.dashboard.progress_area) frm.dashboard.progress_area.body.empty();
        frm.dashboard.add_progress(
            __("{0} of {1} certified", [fmt(certified), fmt(a.subcontract_value)]),
            [{ title: `${pct.toFixed(1)}%`, width: `${pct}%`, progress_class: `progress-bar-${colour}` }]
        );
    });
}
