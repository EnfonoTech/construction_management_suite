frappe.ui.form.on("Variation Order", {
    refresh(frm) {
        frm.set_query("project", () => ({ filters: { status: ["!=", "Completed"] } }));
        frm.set_query("boq_ref", () => ({ filters: { project: frm.doc.project, docstatus: 1 } }));
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
});

frappe.ui.form.on("Variation Order Item", {
    qty: (frm, cdt, cdn) => set_amount(frm, cdt, cdn),
    rate: (frm, cdt, cdn) => set_amount(frm, cdt, cdn),
    nature: (frm) => frm.script_manager.trigger("validate"),
});

function set_amount(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    frappe.model.set_value(cdt, cdn, "amount", flt(row.qty) * flt(row.rate));
}
