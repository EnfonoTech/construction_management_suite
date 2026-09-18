frappe.ui.form.on("Retention Release", {
    onload(frm) {
        CMS.defaultTaxTemplate(frm, "sales_taxes_template");
    },

    taxes_and_charges(frm) { CMS.loadTaxTemplate(frm); },

    taxes_remove(frm) { CMS.recalc(frm); },

    refresh(frm) {
        CMS.filterProjects(frm);
        if (!frm.doc.request_date) frm.set_value("request_date", frappe.datetime.get_today());
        CMS.linkButton(frm, __("Sales Invoice"), "Sales Invoice", frm.doc.sales_invoice_ref);
    },

    project(frm) {
        CMS.fillFromProject(frm, { company: "company", client: "customer" })
            .then(() => frm.doc.project && fetch_retention(frm));
    },

    company(frm) {
        CMS.filterProjects(frm);
        CMS.clearForeignProject(frm);
        if (frm.doc.company) CMS.currencyFromCompany(frm, frm.doc.company);
    },

    release_amount(frm) { CMS.recalc(frm); },
    release_type(frm) { suggest_amount(frm); },
});

/** Read how much has actually been held and released on this project. */
function fetch_retention(frm) {
    frappe.call({
        method: "construction_management_suite.api.boq.get_retention_summary",
        args: { project: frm.doc.project },
        callback: (r) => {
            if (!r.message) return;
            frm.set_value("total_retention_held", r.message.total_held);
            frm.doc.released_to_date = r.message.total_released;
            suggest_amount(frm);
            frm.dashboard.clear_headline();
            const cur = frm.doc.currency || frappe.defaults.get_default("currency");
            frm.dashboard.set_headline(
                // Plain text, no entities: Frappe's show_message only treats a
                // string as HTML when it contains TAGS, so &nbsp; here was
                // rendered literally by the .text() path.
                __("Held {0}   ·   already released {1}   ·   outstanding {2}", [
                    format_currency(r.message.total_held, cur),
                    format_currency(r.message.total_released, cur),
                    format_currency(r.message.net_retention, cur),
                ])
            );
            CMS.recalc(frm);
        },
    });
}

/** Half at Practical Completion, the rest at the end of the defects period. */
function suggest_amount(frm) {
    if (frm.doc.release_amount || !frm.doc.release_type) return CMS.recalc(frm);
    const outstanding = flt(frm.doc.total_retention_held) - flt(frm.doc.released_to_date);
    if (outstanding <= 0) return;
    const share = frm.doc.release_type === "Practical Completion" ? 0.5 : 1;
    frm.set_value("release_amount", flt(outstanding * share)).then(() => CMS.recalc(frm));
}

CMS.liveRows("Sales Taxes and Charges", ["charge_type", "rate", "tax_amount", "row_id"]);
