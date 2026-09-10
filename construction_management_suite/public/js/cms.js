/**
 * Construction Management Suite — Global JS
 * Shared utilities, project dashboard widget, quick entry helpers.
 */

/* ── Global Namespace ── */
window.CMS = window.CMS || {};

CMS.getProjectDashboard = function (project, callback) {
    frappe.call({
        method: "construction_management_suite.api.boq.get_project_cost_dashboard",
        args: { project },
        callback: (r) => callback && callback(r.message),
    });
};

CMS.renderKPICard = function (container, label, value, currency, state) {
    const formatted = format_currency(value, currency);
    const stateClass = state ? `cms-kpi-card ${state}` : "cms-kpi-card";
    $(container).append(`
        <div class="${stateClass}">
            <div class="cms-kpi-value">${formatted}</div>
            <div class="cms-kpi-label">${label}</div>
        </div>
    `);
};

CMS.renderProgressBar = function (container, value, max) {
    const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0;
    const cls = pct > 100 ? "over-budget" : pct > 80 ? "warning" : "";
    $(container).html(`
        <div class="cms-progress-bar">
            <div class="cms-progress-fill ${cls}" style="width:${pct}%"></div>
        </div>
        <small class="text-muted">${pct.toFixed(1)}%</small>
    `);
};

/* ── Quick Actions ── */
CMS.quickCreateDailyReport = function (project) {
    frappe.new_doc("Daily Site Report", { project });
};

CMS.quickCreateSMR = function (project) {
    frappe.new_doc("Site Material Request", { project });
};

/* ── Project form: construction cockpit ── */

frappe.ui.form.on("Project", {
    refresh(frm) {
        if (frm.is_new()) return;

        const make = (label, doctype, extra) => {
            frm.add_custom_button(__(label), () => {
                frappe.new_doc(doctype, Object.assign({
                    project: frm.doc.name,
                    company: frm.doc.company,
                }, extra || {}));
            }, __("Create"));
        };

        // The order a job actually runs in.
        make("BOQ", "BOQ", { client: frm.doc.customer, client_po: frm.doc.cms_client_po });
        make("Cost Estimation", "Cost Estimation");
        make("Variation Order", "Variation Order", { client: frm.doc.customer });
        make("Interim Payment Certificate", "Interim Payment Certificate", {
            client: frm.doc.customer,
            retention_percent: frm.doc.cms_retention_percent,
            contract_value: frm.doc.cms_contract_value,
        });
        make("Daily Site Report", "Daily Site Report");
        make("Subcontract Agreement", "Subcontract Agreement");
        make("Material Forecast", "Material Forecast");

        [
            ["BOQs", "BOQ"],
            ["Certificates", "Interim Payment Certificate"],
            ["Budget", "Project Budget"],
            ["Site Reports", "Daily Site Report"],
            ["Subcontracts", "Subcontract Agreement"],
        ].forEach(([label, doctype]) => {
            frm.add_custom_button(__(label), () => {
                frappe.set_route("List", doctype, { project: frm.doc.name });
            }, __("Construction"));
        });

        render_headline(frm);
    },
});

/** Contract position at a glance, above the form. */
function render_headline(frm) {
    frappe.call({
        method: "construction_management_suite.api.boq.get_project_cost_dashboard",
        args: { project: frm.doc.name },
        callback: (r) => {
            const d = r.message;
            if (!d) return;
            const cur = frm.doc.currency || frappe.defaults.get_default("currency");
            const money = (v) => format_currency(flt(v), cur);
            const billed = (d.billing || {}).total_billed;
            const budget = (d.budget || {}).total_budget;
            const actual = (d.budget || {}).total_actual_cost;
            const contract = flt(frm.doc.cms_contract_value);

            if (!contract && !billed && !budget) return;

            const cells = [
                [__("Contract"), money(contract)],
                [__("Billed"), money(billed) + (contract ? ` (${(flt(billed) / contract * 100).toFixed(0)}%)` : "")],
                [__("Retention held"), money((d.retention || {}).net_retention)],
                [__("Budget"), money(budget)],
                [__("Actual"), money(actual)],
                [__("Complete"), `${flt(frm.doc.percent_complete).toFixed(1)}%`],
            ];
            frm.dashboard.clear_headline();
            frm.dashboard.set_headline(
                `<div style="display:flex;flex-wrap:wrap;gap:6px 26px">` +
                cells.map(([k, v]) =>
                    `<span><span class="text-muted">${k}</span> <b>${v}</b></span>`).join("") +
                `</div>`
            );
        },
    });
}

/* ══════════════════ Shared form helpers ══════════════════
 * Construction documents nearly all hang off a Project, and nearly all of
 * their header fields are already recorded on it. These helpers pull that
 * across so the same six fields are not retyped on every certificate.
 */

CMS.PROJECT_FIELDS = [
    "company", "customer", "cost_center", "cms_contract_value",
    "cms_retention_percent", "cms_client_po", "expected_start_date", "expected_end_date",
];

/** Does this form actually have that field? Avoids set_value warnings. */
CMS.has = function (frm, fieldname) {
    return Boolean(frm.meta.fields.find(f => f.fieldname === fieldname));
};

/** Set a field only when it is still empty, so typed input is never clobbered. */
CMS.fillIfBlank = function (frm, fieldname, value) {
    if (!value) return;
    if (!CMS.has(frm, fieldname)) return;
    if (frm.doc[fieldname]) return;
    frm.set_value(fieldname, value);
};

/**
 * Copy fields across from the linked Project.
 * @param map  {target_fieldname: project_fieldname}
 * @param projectField  defaults to "project"
 */
CMS.fillFromProject = function (frm, map, projectField) {
    const project = frm.doc[projectField || "project"];
    if (!project) return Promise.resolve();
    return frappe.db.get_value("Project", project, CMS.PROJECT_FIELDS).then(r => {
        const p = r.message;
        if (!p) return;
        Object.entries(map).forEach(([target, source]) => CMS.fillIfBlank(frm, target, p[source]));
        if (CMS.has(frm, "currency") && !frm.doc.currency && p.company) {
            return CMS.currencyFromCompany(frm, p.company);
        }
    });
};

CMS.currencyFromCompany = function (frm, company) {
    return frappe.db.get_value("Company", company, "default_currency").then(r => {
        if (r.message) CMS.fillIfBlank(frm, "currency", r.message.default_currency);
    });
};

/** Restrict a link field to the document's own company. */
CMS.filterByCompany = function (frm, fieldname, extra) {
    frm.set_query(fieldname, () => ({
        filters: Object.assign({ company: frm.doc.company }, extra || {}),
    }));
};

/**
 * Restrict a project link to the document's own company, and to jobs that are
 * still running. On a multi-company site an unfiltered project list is how a
 * certificate ends up posted against the wrong company's books.
 */
CMS.filterProjects = function (frm, fieldname) {
    frm.set_query(fieldname || "project", () => ({
        filters: {
            company: frm.doc.company || undefined,
            status: ["!=", "Completed"],
        },
    }));
};

/** Clear a project that no longer belongs to the chosen company. */
CMS.clearForeignProject = function (frm, fieldname) {
    const field = fieldname || "project";
    if (!frm.doc[field] || !frm.doc.company) return;
    frappe.db.get_value("Project", frm.doc[field], "company").then(r => {
        if (r.message && r.message.company !== frm.doc.company) {
            frm.set_value(field, null);
            frappe.show_alert({
                message: __("Project cleared — it belongs to {0}", [r.message.company]),
                indicator: "orange",
            });
        }
    });
};

/** Restrict a link field to the document's own project. */
CMS.filterByProject = function (frm, fieldname, extra) {
    frm.set_query(fieldname, () => ({
        filters: Object.assign({ project: frm.doc.project }, extra || {}),
    }));
};

/** Sum a child table column. */
CMS.sum = function (rows, field) {
    return (rows || []).reduce((t, r) => t + flt(r[field]), 0);
};

/** Recompute a child row's amount = qty × rate, under any field names. */
CMS.rowAmount = function (cdt, cdn, qtyField, rateField, amountField) {
    const row = locals[cdt][cdn];
    frappe.model.set_value(cdt, cdn, amountField, flt(row[qtyField]) * flt(row[rateField]));
};

/** A "go to the document this one created" button. */
CMS.linkButton = function (frm, label, doctype, name) {
    if (!name) return;
    frm.add_custom_button(label, () => frappe.set_route("Form", doctype, name), __("View"));
};

/* ══════════════════ Calculations ══════════════════
 * One pure function per document, mirroring its Python controller exactly.
 * Each takes a doc, mutates it, and touches no Frappe API — so the same code
 * runs in the browser on every keystroke and in the test harness that checks
 * it against the server. Anything needing a database read (retention held to
 * date, previously approved variations) stays server-side and is not here.
 */
CMS.calc = {};

CMS.calc["BOQ"] = function (doc) {
    let mat = 0, lab = 0, eqp = 0, sub = 0, ovh = 0, tot = 0;
    (doc.items || []).forEach(r => {
        r.cost_rate = flt(r.material_rate) + flt(r.labour_rate) + flt(r.equipment_rate)
            + flt(r.subcontract_rate) + flt(r.overhead_rate);
        if (flt(r.margin_percent) && flt(r.cost_rate)) {
            r.rate = flt(r.cost_rate) * (1 + flt(r.margin_percent) / 100);
        }
        r.amount = flt(r.qty) * flt(r.rate);
        r.material_amount = flt(r.qty) * flt(r.material_rate);
        r.labour_amount = flt(r.qty) * flt(r.labour_rate);
        r.equipment_amount = flt(r.qty) * flt(r.equipment_rate);
        r.subcontract_amount = flt(r.qty) * flt(r.subcontract_rate);
        r.overhead_amount = flt(r.qty) * flt(r.overhead_rate);
        r.variance_qty = flt(r.actual_qty) - flt(r.qty);
        r.variance_amount = flt(r.variance_qty) * flt(r.rate);
        mat += r.material_amount; lab += r.labour_amount;
        eqp += r.equipment_amount; sub += r.subcontract_amount;
        ovh += r.overhead_amount; tot += r.amount;
    });
    doc.total_material_amount = mat;
    doc.total_labour_amount = lab;
    doc.total_equipment_amount = eqp;
    doc.total_subcontract_amount = sub;
    doc.total_overhead_amount = ovh;
    doc.total_amount = tot;
    doc.total_cost_amount = (doc.items || []).reduce((t, r) => t + flt(r.qty) * flt(r.cost_rate), 0);
    doc.effective_margin_percent = flt(doc.total_cost_amount)
        ? (flt(doc.total_amount) - flt(doc.total_cost_amount)) / flt(doc.total_cost_amount) * 100 : 0;
    doc.profit_margin_amount = flt(doc.total_amount) * flt(doc.profit_margin_percent) / 100;
    doc.grand_total = flt(doc.total_amount) + flt(doc.profit_margin_amount);
};

CMS.calc["Rate Analysis"] = function (doc) {
    const t = { Material: 0, Labour: 0, Equipment: 0, Subcontract: 0, Overhead: 0 };
    (doc.resources || []).forEach(r => {
        r.amount = flt(r.qty) * flt(r.rate);
        r.net_amount = flt(r.amount) * (1 + flt(r.waste_factor) / 100);
        if (r.resource_type in t) t[r.resource_type] += flt(r.net_amount);
    });
    doc.total_material_cost = t.Material;
    doc.total_labour_cost = t.Labour;
    doc.total_equipment_cost = t.Equipment;
    doc.total_subcontract_cost = t.Subcontract;
    doc.total_overhead_cost = t.Overhead;
    doc.total_cost = t.Material + t.Labour + t.Equipment + t.Subcontract + t.Overhead;
    doc.rate_per_unit = doc.total_cost / (flt(doc.output_qty) || 1);
};

CMS.calc["Cost Estimation"] = function (doc) {
    let m = 0, l = 0, e = 0, sc = 0, o = 0, sub = 0;
    (doc.items || []).forEach(r => {
        // Rows linked to a Rate Analysis are priced by the server from that
        // analysis; leave their unit cost alone.
        if (!r.rate_analysis_ref) {
            r.unit_cost = flt(r.material_cost) + flt(r.labour_cost) + flt(r.equipment_cost)
                + flt(r.subcontract_cost) + flt(r.overhead_cost);
        }
        r.total_cost = flt(r.qty) * flt(r.unit_cost);
        m += flt(r.material_cost) * flt(r.qty);
        l += flt(r.labour_cost) * flt(r.qty);
        e += flt(r.equipment_cost) * flt(r.qty);
        sc += flt(r.subcontract_cost) * flt(r.qty);
        o += flt(r.overhead_cost) * flt(r.qty);
        sub += flt(r.total_cost);
    });
    doc.estimated_material_cost = m;
    doc.estimated_labour_cost = l;
    doc.estimated_equipment_cost = e;
    doc.estimated_subcontract_cost = sc;
    doc.estimated_overhead_cost = o;
    doc.contingency_amount = flt(sub) * flt(doc.contingency_percent) / 100;
    doc.total_estimated_cost = sub + flt(doc.contingency_amount);
    if (flt(doc.selling_price) > 0) {
        doc.margin_percent =
            (flt(doc.selling_price) - flt(doc.total_estimated_cost)) / flt(doc.selling_price) * 100;
    }
};

CMS.calc["Interim Payment Certificate"] = function (doc) {
    let gross = 0;
    (doc.items || []).forEach(r => {
        r.contract_amount = flt(r.contract_qty) * flt(r.contract_rate);
        r.cumulative_qty = flt(r.previous_qty_claimed) + flt(r.qty_this_period);
        r.amount_this_period = flt(r.qty_this_period) * flt(r.contract_rate);
        r.cumulative_amount = flt(r.cumulative_qty) * flt(r.contract_rate);
        r.percent_complete = flt(r.contract_amount) > 0
            ? flt(r.cumulative_amount) / flt(r.contract_amount) * 100 : 0;
        gross += flt(r.amount_this_period);
    });
    doc.gross_amount_this_period = gross;
    doc.cumulative_amount_to_date = flt(doc.previous_cumulative_amount) + gross;
    doc.retention_amount = flt(doc.gross_amount_this_period) * flt(doc.retention_percent) / 100;
    doc.net_payable_this_period = flt(doc.gross_amount_this_period)
        - flt(doc.retention_amount) - flt(doc.advance_recovery_amount) - flt(doc.other_deductions);
};

CMS.calc["Variation Order"] = function (doc) {
    let add = 0, omit = 0;
    (doc.items || []).forEach(r => {
        r.amount = flt(r.qty) * flt(r.rate);
        if (r.nature === "Addition") add += flt(r.amount);
        else if (r.nature === "Omission") omit += flt(r.amount);
    });
    doc.addition_amount = add;
    doc.omission_amount = omit;
    doc.net_variation_amount = add - omit;
};

CMS.calc["Subcontract Agreement"] = function (doc) {
    (doc.items || []).forEach(r => { r.amount = flt(r.qty) * flt(r.rate); });
    doc.advance_amount = flt(doc.subcontract_value) * flt(doc.advance_percent) / 100;
};

CMS.calc["Subcontractor Payment Certificate"] = function (doc) {
    doc.gross_amount_claimed = (doc.items || []).reduce((t, r) => t + flt(r.amount_claimed), 0);
    doc.retention_deduction = flt(doc.certified_amount) * flt(doc.retention_percent) / 100;
    doc.net_payable = flt(doc.certified_amount) - flt(doc.retention_deduction)
        - flt(doc.advance_recovery) - flt(doc.other_deductions);
};

CMS.calc["Subcontractor Work Order"] = function (doc) {
    let contract = 0, done = 0;
    (doc.items || []).forEach(r => {
        r.contract_amount = flt(r.contract_qty) * flt(r.contract_rate);
        r.completed_amount = flt(r.completed_qty) * flt(r.contract_rate);
        r.completion_percent = flt(r.contract_qty) > 0
            ? flt(r.completed_qty) / flt(r.contract_qty) * 100 : 0;
        contract += flt(r.contract_amount);
        done += flt(r.completed_amount);
    });
    doc.total_contract_value = contract;
    doc.total_completed_value = done;
    doc.completion_percent = contract > 0 ? done / contract * 100 : 0;
};

CMS.calc["Material Forecast"] = function (doc) {
    (doc.items || []).forEach(r => {
        r.net_qty_required = flt(r.boq_qty) * (1 + flt(r.waste_factor) / 100);
        r.qty_to_order = Math.max(0, flt(r.net_qty_required) - flt(r.already_ordered_qty));
        r.estimated_value = flt(r.qty_to_order) * flt(r.estimated_rate);
    });
    doc.total_forecast_qty_value = (doc.items || []).reduce((t, r) => t + flt(r.estimated_value), 0);
};

CMS.calc["Material Consumption Entry"] = function (doc) {
    (doc.items || []).forEach(r => { r.amount = flt(r.qty) * flt(r.valuation_rate); });
};

CMS.calc["Project Budget"] = function (doc) {
    (doc.items || []).forEach(r => { r.variance = flt(r.budgeted_amount) - flt(r.actual_amount); });
    doc.variance_amount = flt(doc.total_budget) - flt(doc.total_actual_cost);
    doc.budget_utilization_percent = flt(doc.total_budget) > 0
        ? flt(doc.total_actual_cost) / flt(doc.total_budget) * 100 : 0;
};

CMS.calc["Daily Site Report"] = function (doc) {
    (doc.labour || []).forEach(r => {
        r.daily_cost = flt(r.headcount) * flt(r.daily_rate)
            + flt(r.overtime_hours) * flt(r.overtime_rate);
    });
    (doc.equipment || []).forEach(r => {
        r.cost = (flt(r.hours_worked) + flt(r.idle_hours)) * flt(r.hourly_rate);
    });
};

/**
 * Recalculate and repaint. Called on every keystroke, so the figures on screen
 * are always the ones that will be saved.
 */
CMS.recalc = function (frm) {
    const fn = CMS.calc[frm.doc.doctype];
    if (!fn) return;
    fn(frm.doc);
    frm.refresh_fields();
};

/** Wire every input field of a child table to recalculate the parent. */
CMS.liveRows = function (childDoctype, fields) {
    const handlers = {};
    fields.forEach(f => { handlers[f] = (frm) => CMS.recalc(frm); });
    frappe.ui.form.on(childDoctype, handlers);
};

/**
 * Restrict a child row's UOM to the ones defined on its Item, when Stock
 * Settings says so. ERPNext applies this to its own transactions via the same
 * query; without wiring it, our UOM fields ignore the setting entirely.
 */
CMS.uomQuery = function (frm, tablefield, itemfield) {
    frm.set_query("uom", tablefield, (doc, cdt, cdn) => {
        const row = locals[cdt][cdn];
        return {
            query: "erpnext.controllers.queries.get_item_uom_query",
            filters: { item_code: row[itemfield || "item_code"] },
        };
    });
};

/**
 * Fill a row's rate from the item, when it is still blank.
 * Tells the user where the number came from — a rate off a price list and one
 * off a book valuation deserve different amounts of trust.
 */
CMS.fetchItemRate = function (frm, cdt, cdn, opts) {
    const row = locals[cdt][cdn];
    const item = row[opts.itemfield || "item_code"];
    if (!item || flt(row[opts.target])) return;
    frappe.call({
        method: opts.valuation
            ? "construction_management_suite.api.boq.get_item_valuation_rate"
            : "construction_management_suite.api.boq.get_item_rate",
        args: opts.valuation
            ? { item_code: item, warehouse: opts.warehouse ? frm.doc[opts.warehouse] : null }
            : { item_code: item, company: frm.doc.company },
        callback: (r) => {
            if (!r.message || !flt(r.message.rate)) return;
            frappe.model.set_value(cdt, cdn, opts.target, r.message.rate);
            CMS.recalc(frm);
            frappe.show_alert({
                message: __("{0} rate {1} — {2}", [
                    item,
                    format_currency(r.message.rate, frm.doc.currency),
                    r.message.source,
                ]),
                indicator: "blue",
            }, 4);
        },
    });
};
