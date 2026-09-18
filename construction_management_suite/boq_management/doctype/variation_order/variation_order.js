frappe.ui.form.on("Variation Order", {
    refresh(frm) {
        CMS.uomQuery(frm, "items", "item_code");
        CMS.filterProjects(frm);
        frm.set_query("boq_ref", () => ({ filters: { project: frm.doc.project, docstatus: 1 } }));
        CMS.linkButton(frm, __("BOQ"), "BOQ", frm.doc.boq_ref);

        if (frm.doc.docstatus === 0 && frm.doc.boq_ref) {
            frm.add_custom_button(__("Get Items from BOQ"), () => pick_boq_lines(frm));
        }
    },

    company(frm) {
        CMS.filterProjects(frm);
        CMS.clearForeignProject(frm);
    },

    project(frm) {
        if (!frm.doc.project) return;
        frappe.db.get_value("Project", frm.doc.project, ["customer", "company"]).then(r => {
            if (r.message) {
                frm.set_value("client", r.message.customer);
                frm.set_value("company", r.message.company);
            }
        });
    },
    items_remove(frm) { CMS.recalc(frm); },

});

CMS.liveRows("Variation Order Item", ["nature", "qty", "rate"]);

frappe.ui.form.on("Variation Order Item", {
    item_code(frm, cdt, cdn) {
        CMS.fetchItemRate(frm, cdt, cdn, { itemfield: "item_code", target: "rate" });
    },
});


/** Pick which contract lines this variation touches, rather than dumping the whole bill in. */
function pick_boq_lines(frm) {
    frappe.call({
        method: "construction_management_suite.api.boq.get_boq_lines_for_variation",
        args: { boq: frm.doc.boq_ref },
        callback: (r) => {
            const lines = r.message || [];
            if (!lines.length) return frappe.msgprint(__("That BOQ has no lines"));
            const taken = new Set((frm.doc.items || []).map(i => i.boq_item_ref).filter(Boolean));
            const available = lines.filter(l => !taken.has(l.boq_item_ref));
            if (!available.length) {
                return frappe.msgprint(__("Every line of this BOQ is already on the variation"));
            }

            const d = new frappe.ui.Dialog({
                title: __("Lines from {0}", [frm.doc.boq_ref]),
                size: "large",
                fields: [
                    {
                        fieldname: "nature", fieldtype: "Select", label: __("Add as"),
                        options: ["Omission", "Addition"], default: "Omission",
                        description: __("Omission removes contract work; Addition is extra work at these rates."),
                    },
                    { fieldname: "lines", fieldtype: "HTML" },
                ],
                primary_action_label: __("Add Selected"),
                primary_action() {
                    const chosen = [];
                    d.$wrapper.find("input.cms-pick:checked").each(function () {
                        const line = available[parseInt($(this).data("i"), 10)];
                        chosen.push(Object.assign({}, line, { nature: d.get_value("nature") }));
                    });
                    if (!chosen.length) return frappe.msgprint(__("Nothing selected"));
                    d.hide();
                    frm.call("add_boq_lines", { rows: chosen }).then(res => {
                        frm.refresh_field("items");
                        CMS.recalc(frm);
                        frappe.show_alert({ message: __("{0} line(s) added", [res.message || 0]), indicator: "green" });
                    });
                },
            });

            const rows = available.map((l, i) => `
                <tr>
                  <td><input type="checkbox" class="cms-pick" data-i="${i}"></td>
                  <td>${frappe.utils.escape_html(l.item_no || "")}</td>
                  <td>${frappe.utils.escape_html(l.description || l.item_code || "")}</td>
                  <td class="text-right">${format_number(l.qty, null, 2)} ${frappe.utils.escape_html(l.uom || "")}</td>
                  <td class="text-right">${format_currency(l.rate, frm.doc.currency)}</td>
                </tr>`).join("");
            d.fields_dict.lines.$wrapper.html(`
                <div style="max-height:380px; overflow:auto">
                  <table class="table table-sm">
                    <thead><tr>
                      <th style="width:30px"><input type="checkbox" class="cms-all"></th>
                      <th>${__("Item")}</th><th>${__("Description")}</th>
                      <th class="text-right">${__("Qty")}</th><th class="text-right">${__("Rate")}</th>
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
