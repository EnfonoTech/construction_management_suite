frappe.ui.form.on("Retention Release", {
    refresh(frm) {
        frm.set_query("project", () => ({ filters: { status: ["!=", "Completed"] } }));
        if (!frm.doc.request_date) frm.set_value("request_date", frappe.datetime.get_today());
        CMS.linkButton(frm, __("Sales Invoice"), "Sales Invoice", frm.doc.sales_invoice_ref);
    },

    project(frm) {
        CMS.fillFromProject(frm, { company: "company", client: "customer" })
            .then(() => frm.doc.project && fetch_retention(frm));
    },

    company(frm) {
        if (frm.doc.company) CMS.currencyFromCompany(frm, frm.doc.company);
    },

    release_amount: balance,
    total_retention_held: balance,
});

/** Read how much has actually been held and released on this project. */
function fetch_retention(frm) {
    frappe.call({
        method: "construction_management_suite.api.boq.get_retention_summary",
        args: { project: frm.doc.project },
        callback: (r) => {
            if (!r.message) return;
            CMS.fillIfBlank(frm, "total_retention_held", r.message.total_held);
            frm.dashboard.clear_headline();
            const cur = frm.doc.currency || frappe.defaults.get_default("currency");
            frm.dashboard.set_headline(
                __("Held {0} &nbsp;·&nbsp; already released {1} &nbsp;·&nbsp; outstanding {2}", [
                    format_currency(r.message.total_held, cur),
                    format_currency(r.message.total_released, cur),
                    format_currency(r.message.net_retention, cur),
                ])
            );
            balance(frm);
        },
    });
}

function balance(frm) {
    frm.set_value("balance_retention",
        flt(frm.doc.total_retention_held) - flt(frm.doc.release_amount));
}
