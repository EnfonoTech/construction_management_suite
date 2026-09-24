frappe.ui.form.on("Site Transfer", {
    refresh(frm) {
        if (frm.doc.docstatus === 0 && frm.doc.to_project) {
            frm.add_custom_button(__("Get from the Take-off"), () => {
                frm.call("get_items_from_take_off").then(r => {
                    frm.refresh_field("items");
                    frappe.show_alert(r.message
                        ? __("{0} line(s) added", [r.message])
                        : __("Nothing outstanding on that project"));
                });
            });
        }
        CMS.uomQuery(frm, "items", "item_code");
        CMS.filterByCompany(frm, "from_warehouse", { is_group: 0 });
        CMS.filterByCompany(frm, "to_warehouse", { is_group: 0 });
        CMS.filterProjects(frm, "from_project");
        CMS.filterProjects(frm, "to_project");
        if (!frm.doc.transfer_date) frm.set_value("transfer_date", frappe.datetime.get_today());
        CMS.linkButton(frm, __("Stock Entry"), "Stock Entry", frm.doc.stock_entry_ref);
    },
    to_project(frm) {
        CMS.fillFromProject(frm, { company: "company" }, "to_project");
        fill_store(frm, "to_project", "to_warehouse", "from_warehouse");
    },
    from_project(frm) {
        CMS.fillFromProject(frm, { company: "company" }, "from_project");
        fill_store(frm, "from_project", "from_warehouse", "to_warehouse");
    },
    company(frm) {
        CMS.filterByCompany(frm, "from_warehouse", { is_group: 0 });
        CMS.filterByCompany(frm, "to_warehouse", { is_group: 0 });
        CMS.filterProjects(frm, "from_project");
        CMS.filterProjects(frm, "to_project");
        CMS.clearForeignProject(frm, "from_project");
        CMS.clearForeignProject(frm, "to_project");
    },
    to_warehouse(frm) {
        if (frm.doc.from_warehouse && frm.doc.from_warehouse === frm.doc.to_warehouse) {
            frappe.msgprint(__("Source and destination warehouses must be different"));
            frm.set_value("to_warehouse", null);
        }
    },
});

frappe.ui.form.on("Site Transfer Item", {
    item_code(frm, cdt, cdn) {
        CMS.fetchItemRate(frm, cdt, cdn, { itemfield: "item_code", target: "valuation_rate", valuation: true, warehouse: "from_warehouse" });
    },
});

/** One end's store from its own project — never onto the other end.
 *
 * Both warehouses are mandatory, so the form blocks the save before the server
 * could fill them; it has to happen here. Filling the destination with the
 * source store would only raise the "must be different" error the user had not
 * made yet, so a collision leaves the field blank.
 */
function fill_store(frm, projectField, target, other) {
    if (frm.doc[target]) return;
    CMS.projectStore(frm, projectField).then(store => {
        if (store && !frm.doc[target] && store !== frm.doc[other]) {
            frm.set_value(target, store);
        }
    });
}
