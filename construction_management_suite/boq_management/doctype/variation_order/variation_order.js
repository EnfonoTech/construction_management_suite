frappe.ui.form.on("Variation Order", {
    refresh(frm) {
        CMS.uomQuery(frm, "items", "item_code");
        CMS.filterProjects(frm);
        frm.set_query("boq_ref", () => ({ filters: { project: frm.doc.project, docstatus: 1 } }));
        CMS.linkButton(frm, __("BOQ"), "BOQ", frm.doc.boq_ref);
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
