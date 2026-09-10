frappe.ui.form.on("Rate Analysis", {
    refresh(frm) {
        CMS.uomQuery(frm, "resources", "resource_item");
        const lock = (frm.doc.__onload && frm.doc.__onload.lock_state) || {};

        if (lock.locked) apply_lock(frm, lock);
        if (lock.successor) {
            CMS.linkButton(frm, __("Newer Version"), "Rate Analysis", lock.successor);
        }

        if (!frm.is_new()) {
            frm.add_custom_button(__("New Version"), () => new_version(frm), __("Actions"));
        }
        if (!frm.is_new() && frm.doc.status === "Draft" && frm.doc.rate_basis !== "Manual") {
            frm.add_custom_button(__("Update Cost"), () => update_cost(frm), __("Actions"));
        }
        if (!frm.is_new() && frm.doc.status === "Approved") {
            frm.add_custom_button(__("Apply to BOQ Items"), () => apply_to_boq(frm), __("Actions"));
        }
    },

    rate_basis(frm) {
        if (frm.doc.rate_basis !== "Price List") frm.set_value("buying_price_list", null);
    },

    output_qty(frm) { CMS.recalc(frm); },
    resources_remove(frm) { CMS.recalc(frm); },
});

CMS.liveRows("Rate Analysis Resource", ["resource_type", "qty", "rate", "waste_factor"]);

frappe.ui.form.on("Rate Analysis Resource", {
    resource_item(frm, cdt, cdn) {
        CMS.fetchItemRate(frm, cdt, cdn, {
            itemfield: "resource_item",
            target: "rate",
            basis: frm.doc.rate_basis,
            priceList: frm.doc.buying_price_list,
        });
    },
});

/** Read-only the costed content and say why, naming the documents relying on it. */
function apply_lock(frm, lock) {
    (lock.locked_fields || []).forEach(f => frm.set_df_property(f, "read_only", 1));
    const grid = frm.fields_dict.resources && frm.fields_dict.resources.grid;
    if (grid) {
        grid.df.cannot_add_rows = 1;
        grid.df.cannot_delete_rows = 1;
        frm.refresh_field("resources");
    }

    const names = (lock.references || [])
        .map(r => `<a href="/app/${frappe.router.slug(r.doctype)}/${encodeURIComponent(r.name)}">${r.name}</a>`)
        .join(", ");
    const more = lock.reference_count > (lock.references || []).length
        ? __(" and {0} more", [lock.reference_count - lock.references.length]) : "";

    frm.dashboard.add_comment(
        __("Priced into {0}{1}. The resources and quantities are locked so those documents keep the provenance of their rates. Use <b>Actions &gt; New Version</b> to change the costing — status, name and notes can still be edited here.",
           [names, more]),
        "orange", true
    );
}

function new_version(frm) {
    frappe.confirm(
        __("Copy this analysis to a new Draft you can edit?<br><small>This version stays as it is until the new one is approved.</small>"),
        () => frm.call("make_new_version").then(r => {
            if (r.message) frappe.set_route("Form", "Rate Analysis", r.message);
        })
    );
}

function update_cost(frm) {
    frappe.confirm(
        __("Re-read every item-linked rate from <b>{0}</b>?<br><small>Rows with no Item keep their rates.</small>",
           [frm.doc.rate_basis]),
        () => frm.call("update_cost").then(r => {
            if (r.message) show_cost_report(frm, r.message);
            frm.reload_doc();
        })
    );
}

function show_cost_report(frm, res) {
    const cur = frm.doc.currency;
    const money = v => format_currency(flt(v), cur);

    if (!res.changed.length && !res.missing.length && !res.skipped_no_item.length) {
        frappe.show_alert({ message: __("No changes in cost found"), indicator: "blue" });
        return;
    }

    // The headline is what the unit rate moved to — a count of changed rows
    // hides the thing an estimator actually needs to see.
    let html = `<div style="font-size:13px">
      <p><b>${__("Rate per unit")}</b> ${money(res.before.rate_per_unit)}
         &rarr; <b>${money(res.after.rate_per_unit)}</b>
         <span class="text-muted">(${res.basis}${res.price_list ? " — " + res.price_list : ""})</span></p>`;

    if (res.changed.length) {
        html += `<table class="table table-bordered table-sm"><thead><tr>
            <th>#</th><th>${__("Item")}</th><th class="text-right">${__("Was")}</th>
            <th class="text-right">${__("Now")}</th><th>${__("Source")}</th></tr></thead><tbody>`;
        res.changed.forEach(c => {
            html += `<tr><td>${c.idx}</td><td>${frappe.utils.escape_html(c.resource_item)}</td>
              <td class="text-right">${money(c.old_rate)}</td>
              <td class="text-right"><b>${money(c.new_rate)}</b></td>
              <td>${frappe.utils.escape_html(c.source || "")}</td></tr>`;
        });
        html += `</tbody></table>`;
    } else {
        html += `<p class="text-muted">${__("No rate changed.")}</p>`;
    }

    if (res.skipped_no_item.length) {
        html += `<p class="text-muted">${__("{0} row(s) have no Item and keep their rates: {1}. Update Cost only reprices rows that name an Item.",
            [res.skipped_no_item.length,
             res.skipped_no_item.map(s => frappe.utils.escape_html(s.description || "#" + s.idx)).join(", ")])}</p>`;
    }
    if (res.missing.length) {
        html += `<div class="alert alert-warning" style="font-size:12.5px">${
            __("No {0} on file for: {1}. Those rows kept their existing rates.",
               [res.basis, res.missing.map(m => frappe.utils.escape_html(m.resource_item)).join(", ")])}</div>`;
    }
    html += `</div>`;
    new frappe.ui.Dialog({ title: __("Update Cost"), size: "large",
                           fields: [{ fieldtype: "HTML", options: html }] }).show();
}

function apply_to_boq(frm) {
    if (!frm.doc.item_code) {
        frappe.msgprint({
            title: __("Set an Item first"),
            message: __("This analysis has no Item, so there is no way to tell which BOQ lines it prices."),
            indicator: "orange",
        });
        return;
    }
    frappe.prompt(
        [{
            fieldname: "boq", label: __("BOQ"), fieldtype: "Link", options: "BOQ", reqd: 1,
            get_query: () => ({ filters: { docstatus: 0 } }),
            description: __("Draft BOQs only. Every line for {0} will be priced.", [frm.doc.item_code]),
        }],
        (vals) => {
            frappe.call({
                method: "construction_management_suite.api.boq.apply_rate_analysis_to_boq",
                args: { rate_analysis: frm.doc.name, boq: vals.boq, item_code: frm.doc.item_code },
                freeze: true,
                callback: r => frappe.msgprint(r.message),
            });
        },
        __("Apply to BOQ"), __("Apply")
    );
}
