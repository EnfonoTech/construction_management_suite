/**
 * Read the file before writing anything.
 *
 * An import creates an Item per resource and a Rate Analysis per line of work.
 * The moment to find out the file says "Kgs" where the site says "Kg" is
 * before that, not after — so **Check the File** comes first and writes
 * nothing, and Import is only offered once the file has been read.
 */
frappe.ui.form.on("Estimate Import", {
    refresh(frm) {
        frm.add_custom_button(__("Download Template"), () => {
            open_url_post(
                "/api/method/construction_management_suite.importers.estimate_import.download_template",
                {}
            );
        });

        if (frm.doc.cost_estimation) {
            frm.add_custom_button(__("Cost Estimation"), () => {
                frappe.set_route("Form", "Cost Estimation", frm.doc.cost_estimation);
            }, __("View"));
        }

        if (frm.is_new() || frm.doc.cost_estimation) {
            draw(frm);
            return;
        }

        frm.add_custom_button(__("Check the File"), () => {
            frm.call("check_file").then((r) => {
                frm.reload_doc();
                if (r.message) show(frm, r.message, true);
            });
        }).addClass(frm.doc.status === "Checked" ? "" : "btn-primary");

        if (frm.doc.status === "Checked") {
            frm.add_custom_button(__("Import"), () => {
                frappe.confirm(
                    __("Create the items, rate analyses and cost estimation for {0}? Everything arrives as a draft.",
                       [frm.doc.project]),
                    () => frm.call("import_now").then(() => frm.reload_doc())
                );
            }).addClass("btn-primary");
        }
        draw(frm);
    },

    import_file(frm) {
        // A new file has not been read yet, whatever the last one said.
        if (frm.doc.status === "Checked") frm.set_value("status", "Draft");
    },
});

function draw(frm) {
    if (!frm.doc.log) return;
    frm.call("stored_result").then((r) => r.message && show(frm, r.message, !frm.doc.cost_estimation));
}

function show(frm, data, provisional) {
    const money = (v) => format_currency(flt(v));
    const kinds = Object.entries(data.by_kind || {}).map(([k, n]) => `${k} ${n}`).join(" · ");
    const rows = (data.lines || []).map((l) => `
        <tr>
            <td>${frappe.utils.escape_html(l.section || "")}</td>
            <td>${frappe.utils.escape_html(l.work)}</td>
            <td>${frappe.utils.escape_html(l.category || "")}</td>
            <td class="text-right">${format_number(flt(l.qty), null, 2)} ${frappe.utils.escape_html(l.uom)}</td>
            <td class="text-right">${l.resources}</td>
            <td class="text-right">${money(l.cost)}</td>
        </tr>`).join("");

    const warning = data.existing && provisional
        ? `<p class="text-danger">${__("{0} already has {1}. A project has one estimate — cancel and amend it, or import into another project.",
            [data.project, data.existing])}</p>`
        : "";

    $(frm.fields_dict.result_html.wrapper).html(`
        <p>${__("{0} line(s) of work, {1} resource row(s) — {2}", [data.works, data.rows, kinds])}
           ${provisional ? `<br><span class="text-muted">${__("Nothing has been written yet.")}</span>` : ""}</p>
        ${warning}
        <table class="table table-bordered">
          <thead><tr>
            <th>${__("Section")}</th><th>${__("Work")}</th><th>${__("Category")}</th>
            <th class="text-right">${__("Qty")}</th>
            <th class="text-right">${__("Resources")}</th>
            <th class="text-right">${__("Cost")}</th>
          </tr></thead>
          <tbody>${rows}</tbody>
          <tfoot><tr>
            <th colspan="5" class="text-right">${__("Total")}</th>
            <th class="text-right">${money(data.value)}</th>
          </tr></tfoot>
        </table>`);
}
