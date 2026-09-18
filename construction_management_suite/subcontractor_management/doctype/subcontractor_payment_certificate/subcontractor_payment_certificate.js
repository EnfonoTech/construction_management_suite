frappe.ui.form.on("Subcontractor Payment Certificate", {
    onload(frm) {
        CMS.defaultTaxTemplate(frm, "purchase_taxes_template");
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

        if (frm.doc.docstatus === 0 && frm.doc.subcontract_agreement) {
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
            });
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
            CMS.fillIfBlank(frm, "retention_percent", a.retention_percent);

        });
    },
    certified_amount(frm) { CMS.recalc(frm); },
    retention_percent(frm) { CMS.recalc(frm); },
    advance_recovery(frm) { CMS.recalc(frm); },
    other_deductions(frm) { CMS.recalc(frm); },
    items_remove(frm) { CMS.recalc(frm); },

});

CMS.liveRows("Subcontractor Payment Item", ["amount_claimed"]);

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
