/**
 * Buying straight off the estimate, with no forecast in between.
 *
 * A Material Forecast is optional — plenty of jobs are bought line by line as
 * the work comes up. Without one nothing filled the work reference on a
 * request, so Material Position showed what the estimate needed against one
 * row and what was actually bought against another, and the two never met.
 *
 * This asks the take-off directly for what a project still needs, per line of
 * work, and brings it in with the reference set. From here it rides to the
 * order, the receipt and the invoice on its own.
 */
frappe.ui.form.on("Material Request", {
    refresh(frm) {
        if (frm.doc.docstatus !== 0) return;

        frm.add_custom_button(__("Get from Take-off"), () => pick_from_take_off(frm));
    },
});

function pick_from_take_off(frm) {
    const project = frm.doc.items?.find(r => r.project)?.project;

    const ask = new frappe.ui.Dialog({
        title: __("What does the work still need?"),
        fields: [{
            fieldname: "project", label: __("Project"), fieldtype: "Link",
            options: "Project", reqd: 1, default: project,
        }],
        primary_action_label: __("Show"),
        primary_action(values) {
            ask.hide();
            fetch_outstanding(frm, values.project);
        },
    });
    ask.show();
}

function fetch_outstanding(frm, project) {
    frappe.call({
        method: "construction_management_suite.api.boq.take_off_outstanding",
        args: { project },
        freeze: true,
        callback: (r) => {
            const rows = r.message || [];
            if (!rows.length) {
                frappe.msgprint({
                    message: __("Nothing outstanding — every material the priced work needs is already requested or ordered."),
                    indicator: "orange",
                });
                return;
            }
            choose(frm, project, rows);
        },
    });
}

/** Let the buyer take part of it: rarely is a whole job bought at once. */
function choose(frm, project, rows) {
    const picker = new frappe.ui.Dialog({
        title: __("Outstanding against the take-off"),
        size: "large",
        fields: [{
            fieldname: "lines", fieldtype: "Table", cannot_add_rows: 1,
            in_place_edit: true, data: rows,
            get_data: () => rows,
            fields: [
                { fieldname: "item_code", label: __("Item"), fieldtype: "Link", options: "Item",
                  read_only: 1, in_list_view: 1, columns: 3 },
                { fieldname: "cms_work_no", label: __("For Work"), fieldtype: "Data",
                  read_only: 1, in_list_view: 1, columns: 2 },
                { fieldname: "uom", label: __("UOM"), fieldtype: "Link", options: "UOM",
                  read_only: 1, in_list_view: 1, columns: 2 },
                { fieldname: "qty", label: __("Qty"), fieldtype: "Float", in_list_view: 1, columns: 2 },
                { fieldname: "cms_work_ref", fieldtype: "Data", hidden: 1 },
            ],
        }],
        primary_action_label: __("Add to this request"),
        primary_action() {
            const chosen = (picker.fields_dict.lines.grid.get_selected_children() || []);
            const take = chosen.length ? chosen : rows;
            take.forEach((line) => {
                if (!flt(line.qty)) return;
                frm.add_child("items", {
                    item_code: line.item_code,
                    qty: flt(line.qty),
                    uom: line.uom,
                    project: project,
                    schedule_date: frm.doc.schedule_date,
                    cms_work_ref: line.cms_work_ref,
                    cms_work_no: line.cms_work_no,
                });
            });
            picker.hide();
            frm.refresh_field("items");
            frappe.show_alert({
                message: __("{0} line(s) added, each against the work it is for", [take.length]),
                indicator: "green",
            });
        },
    });
    picker.show();
}
