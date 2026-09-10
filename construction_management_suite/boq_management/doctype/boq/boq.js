frappe.ui.form.on("BOQ", {
    refresh(frm) {
        CMS.uomQuery(frm, "items", "item_code");
        if (!frm.is_new()) frm.add_custom_button(__("Rate Build-up"), () => CMS.showRateBuildUp(frm), __("View"));
        CMS.filterProjects(frm);

        if (frm.doc.docstatus === 1 && frm.doc.status !== "Revised") {
            frm.add_custom_button(__("Create Revision"), () => {
                frappe.confirm(__("Create a new revision of this BOQ?"), () => {
                    frm.call("create_revision").then(r => {
                        if (r.message) {
                            frappe.set_route("Form", "BOQ", r.message);
                        }
                    });
                });
            }, __("Actions"));
        }

        if (frm.doc.docstatus === 1) {
            // Both carry the BOQ's lines across — see api.boq.make_*
            frm.add_custom_button(__("Cost Estimation"), () => {
                frappe.model.open_mapped_doc({
                    method: "construction_management_suite.api.boq.make_cost_estimation",
                    frm: frm,
                });
            }, __("Create"));

            frm.add_custom_button(__("Interim Payment Certificate"), () => {
                frappe.model.open_mapped_doc({
                    method: "construction_management_suite.api.boq.make_interim_payment_certificate",
                    frm: frm,
                });
            }, __("Create"));

            frm.add_custom_button(__("Variation Order"), () => {
                frappe.new_doc("Variation Order", {
                    project: frm.doc.project,
                    boq_ref: frm.doc.name,
                    company: frm.doc.company,
                    currency: frm.doc.currency,
                    client: frm.doc.client,
                });
            }, __("Create"));
        }

        if (frm.doc.docstatus === 0) {
            frm.add_custom_button(__("Price from Rate Library"), () => price_from_library(frm), __("Actions"));
            show_rate_drift(frm);

            frm.add_custom_button(__("Import from Template"), () => {
                frappe.prompt(
                    [{ fieldname: "template", label: __("BOQ Template"), fieldtype: "Link", options: "BOQ Template", reqd: 1 }],
                    (values) => {
                        frm.call("import_from_template", { template_name: values.template })
                            .then(() => frm.refresh_fields());
                    },
                    __("Select BOQ Template")
                );
            }, __("Actions"));
        }

        // Show cost breakdown summary chart
        if (frm.doc.total_amount > 0) {
            render_cost_breakdown(frm);
        }
    },

    project(frm) {
        CMS.fillFromProject(frm, {
            company: "company",
            client: "customer",
            client_po: "cms_client_po",
            contract_value: "cms_contract_value",
        });
    },

    company(frm) {
        CMS.filterProjects(frm);
        CMS.clearForeignProject(frm);
        if (frm.doc.company) CMS.currencyFromCompany(frm, frm.doc.company);
    },

    profit_margin_percent(frm) { CMS.recalc(frm); },
    items_remove(frm) { CMS.recalc(frm); },

});



function render_cost_breakdown(frm) {
    const data = [
        { label: __("Material"), value: frm.doc.total_material_amount },
        { label: __("Labour"), value: frm.doc.total_labour_amount },
        { label: __("Equipment"), value: frm.doc.total_equipment_amount },
        { label: __("Overhead"), value: frm.doc.total_overhead_amount },
    ].filter(d => d.value > 0);

    if (!data.length) return;
    frm.dashboard.add_section(
        frappe.render_template(`<div class="cms-cost-breakdown">
            <strong>{{ __("Cost Breakdown") }}</strong>
            <table class="table table-bordered table-sm mt-2">
                {% for row in data %}
                <tr><td>{{ row.label }}</td><td class="text-right">{{ format_currency(row.value, currency) }}</td></tr>
                {% endfor %}
            </table>
        </div>`, { data, currency: frm.doc.currency }),
        __("Cost Breakdown")
    );
}

CMS.liveRows("BOQ Item", ["qty", "rate", "material_rate", "labour_rate", "equipment_rate",
                          "subcontract_rate", "overhead_rate", "actual_qty"]);

// Picking an item offers the library rate; picking an analysis pulls it in.
frappe.ui.form.on("BOQ Item", {
    item_code(frm, cdt, cdn) {
        const row = locals[cdt][cdn];
        if (!row.item_code || row.rate_analysis_ref) return;
        frappe.call({
            method: "construction_management_suite.api.boq.get_rate_analysis_for_item",
            args: { item_code: row.item_code },
            callback: (r) => {
                if (r.message) frappe.model.set_value(cdt, cdn, "rate_analysis_ref", r.message);
            },
        });
    },

    rate_analysis_ref(frm, cdt, cdn) {
        const row = locals[cdt][cdn];
        if (!row.rate_analysis_ref) return;
        frappe.call({
            method: "construction_management_suite.api.boq.get_rate_analysis_rates",
            args: { rate_analysis: row.rate_analysis_ref },
            callback: (r) => {
                if (!r.message) return;
                Object.entries(r.message).forEach(([f, v]) => frappe.model.set_value(cdt, cdn, f, v));
                CMS.recalc(frm);
            },
        });
    },
});

function price_from_library(frm) {
    frappe.confirm(
        __("Price every line that has an approved Rate Analysis for its item?<br><small>Lines that already carry a rate are left alone.</small>"),
        () => {
            frappe.call({
                method: "construction_management_suite.api.boq.price_boq_from_library",
                args: { boq: frm.doc.name },
                freeze: true,
                freeze_message: __("Pricing from the rate library…"),
                callback: (r) => {
                    if (!r.message) return;
                    const m = r.message;
                    let msg = __("{0} line(s) priced.", [m.priced.length]);
                    if (m.skipped.length) msg += "<br>" + __("{0} already had a rate and were left alone.", [m.skipped.length]);
                    if (m.unmatched.length) msg += "<br>" + __("No approved analysis for: {0}", [m.unmatched.join(", ")]);
                    frappe.msgprint({ title: __("Priced from Library"), message: msg, indicator: m.priced.length ? "green" : "orange" });
                    frm.reload_doc();
                },
            });
        }
    );
}

/** Warn when the rate library has moved on since this bill was priced. */
function show_rate_drift(frm) {
    if (frm.is_new()) return;
    frappe.call({
        method: "construction_management_suite.api.boq.check_rate_drift",
        args: { boq: frm.doc.name },
        callback: (r) => {
            const rows = r.message || [];
            if (!rows.length) return;
            const cur = frm.doc.currency;
            frm.dashboard.add_comment(
                __("{0} line(s) were priced from a Rate Analysis that has since changed: {1}. The BOQ keeps the rate it was priced at — use <b>Price from Rate Library</b> with overwrite to take the new ones.",
                   [rows.length, rows.map(d => `#${d.idx} ${d.item_code} (${format_currency(d.applied, cur)} → ${format_currency(d.current, cur)})`).join(", ")]),
                "orange", true
            );
        },
    });
}
