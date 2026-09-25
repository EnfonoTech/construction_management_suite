/**
 * Getting a purchase right while it is being typed.
 *
 * Every control this module has was a refusal at the moment of saving: the
 * buyer filled a whole document and only then learnt it was for work the job
 * is not doing, or for material nobody planned. Two things move that earlier.
 *
 * The work picker offers the project's own lines of work and nothing else, so
 * the commonest mistake — a purchase attributed to another job's work — cannot
 * be made. And choosing a material says what the plan holds for it and how much
 * of it is still to buy, which is the figure that stops a second lorry of
 * cement being ordered.
 */

const CMS_BUYING = ["Material Request", "Purchase Order", "Purchase Receipt", "Purchase Invoice"];

CMS_BUYING.forEach((doctype) => {
    frappe.ui.form.on(doctype, {
        refresh(frm) {
            // Once a line says which work it is for, the item list is that
            // work's own materials. Clearing the work widens it again, which is
            // how something the plan never foresaw is still bought — with the
            // warning that goes with it.
            frm.set_query("item_code", "items", (doc, cdt, cdn) => {
                const row = CMS.row(cdt, cdn);
                const filters = { is_purchase_item: 1 };
                const planned = materials_for(frm, row.cms_work_item);
                if (planned.length) filters.name = ["in", planned];
                return { query: "erpnext.controllers.queries.item_query", filters };
            });

            frm.set_query("cms_work_item", "items", (doc, cdt, cdn) => {
                const row = CMS.row(cdt, cdn);
                const d = CMS.queryDoc(doc, frm);
                return {
                    query: "construction_management_suite.api.boq.work_items_for_project",
                    // The project is on the line here, and on the header of the
                    // documents that have one; either will do.
                    filters: { project: row.project || d.project || project_of(frm) },
                };
            });
            show_the_plan(frm);
        },
    });

    frappe.ui.form.on(`${doctype} Item`, {
        cms_work_item(frm, cdt, cdn) { say_where_it_stands(frm, cdt, cdn); },
        item_code(frm, cdt, cdn) { say_where_it_stands(frm, cdt, cdn); },
        // The quantity is half the question: a planned material is worth
        // nothing said until more of it is asked for than the plan has left.
        qty(frm, cdt, cdn) { say_where_it_stands(frm, cdt, cdn); },
        // On these documents the project arrives on the line, so the plan can
        // only be fetched once a line has one. Without this a new invoice never
        // learnt which job it was for and both the panel and the item filter
        // stayed empty.
        project(frm) { show_the_plan(frm); },
    });
});

/** What the plan holds for this row, in an alert, as it is chosen. */
function say_where_it_stands(frm, cdt, cdn) {
    const row = CMS.row(cdt, cdn);
    if (!row.item_code) return;
    const project = row.project || frm.doc.project;
    if (!project) return;

    frappe.call({
        method: "construction_management_suite.api.boq.line_against_plan",
        args: {
            project, item_code: row.item_code,
            work_item: row.cms_work_item, qty: row.qty,
        },
        callback: (r) => {
            const answer = r.message || {};
            if (!answer.message) return;
            // Nothing is said about a line that fits the plan — see
            // api.boq.line_against_plan.
            const indicator = {
                over: "orange", unplanned: "orange",
                "wrong-work": "orange", "no-plan": "red",
            }[answer.state] || "blue";
            frappe.show_alert({
                message: `${__("Row {0}", [row.idx])}: ${answer.message}`,
                indicator,
            }, 7);
        },
    });
}


/** The job this document is buying for. On these documents it sits per line. */
function project_of(frm) {
    return frm.doc.project || (frm.doc.items || []).map(r => r.project).find(Boolean) || null;
}

/**
 * The money left on the job, and where every material stands against the plan.
 *
 * Both figures existed — one on the Project Budget, one in Material Position —
 * and neither was anywhere near the document being written, so orders were
 * written against a plan nobody was looking at.
 */
function show_the_plan(frm) {
    const project = project_of(frm);
    if (!project) return;
    if (frm.__cms_plan_for === project && frm.__cms_plan_rendered) return;
    frm.__cms_plan_for = project;

    frappe.call({
        method: "construction_management_suite.api.boq.get_buying_context",
        args: { project },
        callback: (r) => {
            const data = r.message;
            if (!data) return;
            frm.__cms_plan_rendered = true;
            frm.__cms_plan_lines = data.lines || [];
            render_budget_headline(frm, data);
            render_position(frm, data);
        },
    });
}

function render_budget_headline(frm, data) {
    const b = data.budget || {};
    if (!b.name) return;
    const money = (v) => format_currency(flt(v), data.currency);
    const short = flt(b.left) < 0;
    const cells = [
        [__("Project"), data.project],
        [__("Budget"), `${money(b.total)} <span class="text-muted">(${__(b.status || "")})</span>`],
        [__("Spent"), money(b.actual)],
        [__("On order"), money(b.committed)],
        [__("Left"), short ? `<span class="text-danger">${money(b.left)}</span>` : money(b.left)],
    ];
    frm.dashboard.clear_headline();
    frm.dashboard.set_headline(
        `<div style="display:flex;flex-wrap:wrap;gap:6px 26px">` +
        cells.map(([k, v]) => `<span><span class="text-muted">${k}</span> <b>${v}</b></span>`).join("") +
        `</div>`
    );
}

function render_position(frm, data) {
    const lines = data.lines || [];
    if (!lines.length) return;
    const num = (v) => format_number(flt(v), null, 2);
    const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));

    const rows = lines.map((l) => {
        // Bought against no plan at all, or more than the plan asked for: the
        // two things worth seeing without reading the numbers.
        const unplanned = !flt(l.required);
        const over = flt(l.required) && flt(l.ordered) > flt(l.required) + 0.0001;
        const tag = unplanned
            ? `<span class="cms-wb-tag warn">${__("not in the plan")}</span>`
            : (over ? `<span class="cms-wb-tag warn">${__("over")}</span>` : "");
        return `
            <tr>
                <td>${esc(l.item_code)}${tag}</td>
                <td>${esc(l.work || "")}</td>
                <td>${esc(l.uom || "")}</td>
                <td class="cms-wb-num">${num(l.required)}</td>
                <td class="cms-wb-num">${num(l.ordered)}</td>
                <td class="cms-wb-num">${num(l.received)}</td>
                <td class="cms-wb-num">${num(l.consumed)}</td>
                <td class="cms-wb-num">${num(l.balance)}</td>
            </tr>`;
    }).join("");

    const html = `
        <div class="cms-wb" style="max-height:320px;overflow:auto">
          <table class="cms-wb-table">
            <thead><tr>
              <th>${__("Item")}</th><th>${__("For Work")}</th><th>${__("UOM")}</th>
              <th class="cms-wb-num">${__("Required")}</th>
              <th class="cms-wb-num">${__("Ordered")}</th>
              <th class="cms-wb-num">${__("Received")}</th>
              <th class="cms-wb-num">${__("Consumed")}</th>
              <th class="cms-wb-num">${__("Left to Use")}</th>
            </tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>`;
    frm.dashboard.add_section(html, __("Against the plan — {0}", [data.project]));
}


/** The materials the plan holds for one line of work, from the panel's own data. */
function materials_for(frm, work) {
    if (!work) return [];
    return (frm.__cms_plan_lines || [])
        .filter((l) => l.work === work && flt(l.required))
        .map((l) => l.item_code);
}
