frappe.ui.form.on("BOQ Template", {
    refresh(frm) {
        CMS.uomQuery(frm, "items", "item_code");
        if (!frm.is_new()) {
            frm.add_custom_button(__("BOQ from this Template"), () => {
                frappe.new_doc("BOQ", {}, (doc) => {
                    doc.__cms_template = frm.doc.name;
                    frappe.msgprint(__("Pick a project, then use Actions → Import from Template."));
                });
            }, __("Create"));
        }
    },
});

frappe.ui.form.on("BOQ Template Item", {
    qty: (frm, cdt, cdn) => CMS.rowAmount(cdt, cdn, "qty", "rate", "amount"),
    rate: (frm, cdt, cdn) => CMS.rowAmount(cdt, cdn, "qty", "rate", "amount"),
});

frappe.ui.form.on("BOQ Template Item", {
    item_code(frm, cdt, cdn) {
        CMS.fetchItemRate(frm, cdt, cdn, { itemfield: "item_code", target: "rate" });
    },
});
