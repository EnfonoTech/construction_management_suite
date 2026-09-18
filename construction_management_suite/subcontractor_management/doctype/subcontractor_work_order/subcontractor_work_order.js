frappe.ui.form.on("Subcontractor Work Order", {
    refresh(frm) {
        frm.set_query("subcontract_agreement", () => ({
            filters: Object.assign({ docstatus: 1 },
                frm.doc.project ? { project: frm.doc.project } : {}),
        }));
        show_progress(frm);

        if (frm.doc.docstatus === 0 && frm.doc.subcontract_agreement) {
            frm.add_custom_button(__("Get Scope from Agreement"), () => {
                frm.call("get_scope_from_agreement").then(r => {
                    frm.refresh_field("items");
                    CMS.recalc(frm);
                    const m = r.message || {};
                    const parts = [];
                    if (m.added) parts.push(__("{0} line(s) added", [m.added]));
                    if (m.topped_up) parts.push(__("{0} topped up", [m.topped_up]));
                    if (!parts.length) {
                        return frappe.msgprint({
                            title: __("Nothing left"),
                            indicator: "orange",
                            message: __("Every line of this agreement has already been delivered."),
                        });
                    }
                    frappe.show_alert({ message: parts.join(", "), indicator: "green" });

                    // The quantity is offered, but another order still holds it.
                    // Instructing the same work twice is a real mistake, so say so.
                    const open = m.open_elsewhere || [];
                    if (!open.length) return;
                    const rows = open.map(c => c.orders.map(o => `
                        <tr>
                          <td>${frappe.utils.escape_html(c.description)}</td>
                          <td><a href="/app/subcontractor-work-order/${encodeURIComponent(o.order)}">${o.order}</a></td>
                          <td>${frappe.utils.escape_html(o.status || "")}</td>
                          <td class="text-right">${format_number(o.instructed, null, 2)}</td>
                          <td class="text-right">${format_number(o.completed, null, 2)}</td>
                        </tr>`).join("")).join("");
                    frappe.msgprint({
                        title: __("Also open on another order"),
                        indicator: "orange",
                        message: `<p>${__("These lines are still open elsewhere. Remove them here if that order will finish the work.")}</p>
                            <table class="table table-sm">
                              <thead><tr><th>${__("Line")}</th><th>${__("Order")}</th>
                                <th>${__("Status")}</th><th class="text-right">${__("Instructed")}</th>
                                <th class="text-right">${__("Built")}</th></tr></thead>
                              <tbody>${rows}</tbody>
                            </table>`,
                    });
                });
            });
        }
        if (frm.doc.docstatus === 1) {
            frm.add_custom_button(__("Payment Certificate"), () => {
                frappe.new_doc("Subcontractor Payment Certificate", {
                    subcontract_agreement: frm.doc.subcontract_agreement,
                    project: frm.doc.project, subcontractor: frm.doc.subcontractor,
                });
            }, __("Create"));
        }
        CMS.linkButton(frm, __("Agreement"), "Subcontract Agreement", frm.doc.subcontract_agreement);
    },

    subcontract_agreement(frm) {
        if (!frm.doc.subcontract_agreement) return;
        frappe.db.get_value("Subcontract Agreement", frm.doc.subcontract_agreement,
            ["project", "subcontractor", "company", "currency", "start_date", "end_date"]
        ).then(r => {
            const a = r.message;
            if (!a) return;
            ["project", "subcontractor", "company", "currency", "start_date", "end_date"]
                .forEach(f => CMS.fillIfBlank(frm, f, a[f]));
        });
    },
    items_remove(frm) { CMS.recalc(frm); },

});

CMS.liveRows("Subcontractor Work Order Item", ["contract_qty", "contract_rate", "completed_qty"]);


/** How much of what was instructed is actually built. */
function show_progress(frm) {
    if (frm.is_new() || !flt(frm.doc.total_contract_value)) return;
    if (frm.dashboard.progress_area) frm.dashboard.progress_area.body.empty();
    const pct = Math.min(Math.max(flt(frm.doc.completion_percent), 0), 100);
    const colour = pct >= 100 ? "green" : pct >= 50 ? "blue" : "orange";
    const fmt = v => format_currency(v, frm.doc.currency);
    frm.dashboard.add_progress(
        __("{0} of {1} complete", [fmt(frm.doc.total_completed_value), fmt(frm.doc.total_contract_value)]),
        [{ title: `${pct.toFixed(1)}%`, width: `${pct}%`, progress_class: `progress-bar-${colour}` }]
    );
}
