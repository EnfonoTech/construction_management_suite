frappe.ui.form.on("BOQ", {
    refresh(frm) {
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
            frm.add_custom_button(__("Cost Estimation"), () => {
                frappe.new_doc("Cost Estimation", {
                    project: frm.doc.project, boq_ref: frm.doc.name,
                    company: frm.doc.company, currency: frm.doc.currency,
                });
            }, __("Create"));

            frm.add_custom_button(__("Interim Payment Certificate"), () => {
                frappe.new_doc("Interim Payment Certificate", {
                    project: frm.doc.project, boq_ref: frm.doc.name,
                    company: frm.doc.company, currency: frm.doc.currency,
                    client: frm.doc.client, contract_value: frm.doc.grand_total,
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

    qty(frm, cdt, cdn) { calculate_row(frm, cdt, cdn); },
    rate(frm, cdt, cdn) { calculate_row(frm, cdt, cdn); },
    material_rate(frm, cdt, cdn) { calculate_row(frm, cdt, cdn); },
    labour_rate(frm, cdt, cdn) { calculate_row(frm, cdt, cdn); },
    equipment_rate(frm, cdt, cdn) { calculate_row(frm, cdt, cdn); },
    overhead_rate(frm, cdt, cdn) { calculate_row(frm, cdt, cdn); },

    profit_margin_percent(frm) {
        frm.doc.profit_margin_amount = flt(frm.doc.total_amount) * flt(frm.doc.profit_margin_percent) / 100;
        frm.doc.grand_total = flt(frm.doc.total_amount) + flt(frm.doc.profit_margin_amount);
        frm.refresh_fields(["profit_margin_amount", "grand_total"]);
    },

    items_remove(frm) { recalculate_totals(frm); },
});

function calculate_row(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    row.amount = flt(row.qty) * flt(row.rate);
    row.material_amount = flt(row.qty) * flt(row.material_rate);
    row.labour_amount = flt(row.qty) * flt(row.labour_rate);
    row.equipment_amount = flt(row.qty) * flt(row.equipment_rate);
    row.overhead_amount = flt(row.qty) * flt(row.overhead_rate);
    frappe.model.set_value(cdt, cdn, "amount", row.amount);
    recalculate_totals(frm);
}

function recalculate_totals(frm) {
    let mat = 0, lab = 0, eqp = 0, ovh = 0, total = 0;
    (frm.doc.items || []).forEach(row => {
        mat += flt(row.material_amount);
        lab += flt(row.labour_amount);
        eqp += flt(row.equipment_amount);
        ovh += flt(row.overhead_amount);
        total += flt(row.amount);
    });
    frm.set_value("total_material_amount", mat);
    frm.set_value("total_labour_amount", lab);
    frm.set_value("total_equipment_amount", eqp);
    frm.set_value("total_overhead_amount", ovh);
    frm.set_value("total_amount", total);
    frm.set_value("profit_margin_amount", flt(total) * flt(frm.doc.profit_margin_percent) / 100);
    frm.set_value("grand_total", flt(total) + flt(frm.doc.profit_margin_amount));
}

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
