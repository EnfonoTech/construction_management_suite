frappe.ui.form.on("Subcontract Agreement", {
    onload(frm) {
        CMS.defaultTaxTemplate(frm, "purchase_taxes_template");
    },

    taxes_and_charges(frm) { CMS.loadTaxTemplate(frm); },

    taxes_remove(frm) { CMS.recalc(frm); },

    refresh(frm) {
        frm.set_query("boq_ref", () => ({
            filters: { project: frm.doc.project, docstatus: 1 },
        }));
        if (frm.doc.docstatus === 0 && frm.doc.boq_ref) {
            frm.add_custom_button(__("Get Items from BOQ"), () => pick_boq_lines(frm));
        }
        CMS.uomQuery(frm, "items", "item_code");
        CMS.filterProjects(frm);
        CMS.linkButton(frm, __("Purchase Order"), "Purchase Order", frm.doc.purchase_order_ref);
        show_position(frm);

        if (frm.doc.docstatus === 1 && frm.doc.status !== "Terminated") {
            frm.add_custom_button(__("Payment Certificate"), () => {
                frappe.new_doc("Subcontractor Payment Certificate", {
                    subcontract_agreement: frm.doc.name,
                    project: frm.doc.project, subcontractor: frm.doc.subcontractor,
                    company: frm.doc.company, currency: frm.doc.currency,
                    retention_percent: frm.doc.retention_percent,
                });
                // The schedule is pulled once the form is up, because it spans
                // every order under the agreement rather than one of them.
                frappe.show_alert(__("Use Get Completed Work to pull the schedule"));
            }, __("Create"));
            frm.add_custom_button(__("Work Order"), () => {
                frappe.new_doc("Subcontractor Work Order", {
                    subcontract_agreement: frm.doc.name,
                });
            }, __("Create"));
        }
    },

    project(frm) {
        CMS.fillFromProject(frm, { company: "company", end_date: "expected_end_date" });
    },

    company(frm) {
        CMS.filterProjects(frm);
        CMS.clearForeignProject(frm);
        if (frm.doc.company) CMS.currencyFromCompany(frm, frm.doc.company);
    },
    subcontract_value(frm) { CMS.recalc(frm); },
    advance_percent(frm) { CMS.recalc(frm); },
    items_remove(frm) { CMS.recalc(frm); },

});



/** Offer the line total as the contract value while the agreement is still a draft. */

CMS.liveRows("Subcontract Item", ["qty", "rate"]);

frappe.ui.form.on("Subcontract Item", {
    item_code(frm, cdt, cdn) {
        CMS.fetchItemRate(frm, cdt, cdn, { itemfield: "item_code", target: "rate" });
    },
});

CMS.liveRows("Purchase Taxes and Charges", ["charge_type", "rate", "tax_amount", "row_id"]);


/** Pick the contract lines this trade is engaged to deliver. */
function pick_boq_lines(frm) {
    frappe.call({
        method: "construction_management_suite.api.boq.get_boq_lines_for_subcontract",
        args: { boq: frm.doc.boq_ref },
        callback: (r) => {
            const lines = r.message || [];
            const taken = new Set((frm.doc.items || []).map(i => i.boq_ref).filter(Boolean));
            const available = lines.filter(l => !taken.has(l.boq_ref));
            if (!available.length) {
                return frappe.msgprint(__("Every line of this BOQ is already on the agreement"));
            }
            const d = new frappe.ui.Dialog({
                title: __("Lines from {0}", [frm.doc.boq_ref]),
                size: "large",
                fields: [{ fieldname: "lines", fieldtype: "HTML" }],
                primary_action_label: __("Add Selected"),
                primary_action() {
                    const chosen = [];
                    d.$wrapper.find("input.cms-pick:checked").each(function () {
                        chosen.push(available[parseInt($(this).data("i"), 10)]);
                    });
                    if (!chosen.length) return frappe.msgprint(__("Nothing selected"));
                    d.hide();
                    frm.call("add_boq_lines", { rows: chosen }).then(res => {
                        frm.refresh_field("items");
                        CMS.recalc(frm);
                        frappe.show_alert({
                            message: __("{0} line(s) added — enter the agreed rate for each",
                                        [res.message || 0]),
                            indicator: "green",
                        });
                    });
                },
            });
            const rows = available.map((l, i) => `
                <tr>
                  <td><input type="checkbox" class="cms-pick" data-i="${i}"></td>
                  <td>${frappe.utils.escape_html(l.boq_item_no || "")}</td>
                  <td>${frappe.utils.escape_html(l.description || l.item_code || "")}</td>
                  <td class="text-right">${format_number(l.qty, null, 2)} ${frappe.utils.escape_html(l.uom || "")}</td>
                  <td class="text-right">${format_currency(l.boq_cost_rate, frm.doc.currency)}</td>
                </tr>`).join("");
            d.fields_dict.lines.$wrapper.html(`
                <div style="max-height:380px; overflow:auto">
                  <table class="table table-sm">
                    <thead><tr>
                      <th style="width:30px"><input type="checkbox" class="cms-all"></th>
                      <th>${__("Item")}</th><th>${__("Description")}</th>
                      <th class="text-right">${__("Qty")}</th>
                      <th class="text-right">${__("Costed At")}</th>
                    </tr></thead>
                    <tbody>${rows}</tbody>
                  </table>
                </div>`);
            d.$wrapper.find("input.cms-all").on("change", function () {
                d.$wrapper.find("input.cms-pick").prop("checked", this.checked);
            });
            d.show();
        },
    });
}


/** Where this agreement stands: certified, paid, and what is still to come. */
function show_position(frm) {
    if (frm.is_new() || frm.doc.docstatus !== 1 || !flt(frm.doc.subcontract_value)) return;
    const fmt = v => format_currency(v, frm.doc.currency);
    const pct = Math.min(flt(frm.doc.total_certified) / flt(frm.doc.subcontract_value) * 100, 100);
    const colour = pct >= 100 ? "green" : pct >= 50 ? "blue" : "orange";
    if (frm.dashboard.progress_area) frm.dashboard.progress_area.body.empty();
    frm.dashboard.add_progress(
        __("{0} of {1} certified   ·   {2} paid   ·   {3} still to come", [
            fmt(frm.doc.total_certified), fmt(frm.doc.subcontract_value),
            fmt(frm.doc.total_paid), fmt(frm.doc.balance_due),
        ]),
        [{ title: `${pct.toFixed(1)}%`, width: `${pct}%`, progress_class: `progress-bar-${colour}` }]
    );
}
