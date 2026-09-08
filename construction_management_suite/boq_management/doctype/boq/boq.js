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

CMS.liveRows("BOQ Item", ["qty", "rate", "material_rate", "labour_rate", "equipment_rate", "overhead_rate", "actual_qty"]);
