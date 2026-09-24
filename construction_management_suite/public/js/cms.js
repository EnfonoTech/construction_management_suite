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
        // A job's store belongs to the job's own company, and a warehouse from
        // another one would be filled onto every material document and then
        // refused by ERPNext at the far end.
        frm.set_query("cms_default_warehouse", () => ({
            filters: { company: frm.doc.company, is_group: 0 },
        }));
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
    "company", "customer", "cost_center", "cms_default_warehouse", "cms_contract_value",
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

/**
 * The store a project keeps, cached on the form.
 *
 * A job has one site store the way it has one cost centre, and a project that
 * runs several deliberately names none — then this answers null and the entry
 * still has to say which. Never fills a field somebody has already answered.
 */
CMS.projectStore = function (frm, projectField) {
    const project = frm.doc[projectField || "project"];
    if (!project) return Promise.resolve(null);
    const cache = frm.__cms_store;
    if (cache && cache.project === project) return Promise.resolve(cache.warehouse);
    return frappe.db.get_value("Project", project, "cms_default_warehouse").then(r => {
        const store = (r.message || {}).cms_default_warehouse || null;
        frm.__cms_store = { project, warehouse: store };
        return store;
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

/**
 * Restrict a line's Rate Analysis picker to analyses for that line's item.
 * Obsolete ones are excluded rather than restricting to Approved, because a
 * Draft analysis is a legitimate pick while a tender is still being priced.
 */
CMS.rateAnalysisQuery = function (frm, tablefield) {
    // Asked once per page and remembered: the picker's callback has to answer
    // synchronously, and the setting cannot be read with frappe.db — the
    // Single is readable by managers only.
    CMS.loadRateScope();
    frm.set_query("rate_analysis_ref", tablefield || "items", (doc, cdt, cdn) => {
        const row = locals[cdt][cdn];
        // is_active covers retirement on its own; an Obsolete analysis is
        // forced inactive on save, so one filter says both.
        const filters = { is_active: 1 };
        // With no item chosen, filtering on an empty item_code would match only
        // analyses that have none — which reads as a broken picker.
        if (row.item_code) filters.item_code = row.item_code;
        // The server refuses another company's analysis while the library is
        // company-specific, so never offer one.
        if (CMS._rateScope && doc.company) filters.company = doc.company;
        return { filters };
    });
};

/** Is the rate library private per company? Cached; defaults to yes. */
CMS._rateScope = 1;
CMS.loadRateScope = function () {
    if (CMS._rateScopeLoaded) return;
    CMS._rateScopeLoaded = true;
    frappe.call({
        method: "construction_management_suite.api.boq.rate_library_is_company_scoped",
        callback: (r) => { CMS._rateScope = Number(r.message) ? 1 : 0; },
    });
};

/** Load a tax template's rows into the document, ERPNext's own way.
 *
 * Setting the template alone leaves the table empty; the server fills it on
 * save, but a user typing quantities wants the tax to move with them.
 */
CMS.loadTaxTemplate = function (frm) {
    if (!frm.doc.taxes_and_charges) {
        frm.clear_table("taxes");
        frm.refresh_field("taxes");
        return Promise.resolve();
    }
    const master = frm.fields_dict.taxes_and_charges.df.options;
    return frappe.call({
        method: "erpnext.controllers.accounts_controller.get_taxes_and_charges",
        args: { master_doctype: master, master_name: frm.doc.taxes_and_charges },
    }).then((r) => {
        frm.clear_table("taxes");
        (r.message || []).forEach((row) => frm.add_child("taxes", row));
        frm.refresh_field("taxes");
        CMS.recalc(frm);
    });
};

/** On a new document, take the module default and load its rows straight away. */
CMS.defaultTaxTemplate = function (frm, setting) {
    if (!frm.is_new() || frm.doc.taxes_and_charges || (frm.doc.taxes || []).length) return;
    frappe.db.get_single_value("Construction Settings", setting).then((template) => {
        if (!template) return;
        frm.set_value("taxes_and_charges", template).then(() => CMS.loadTaxTemplate(frm));
    });
};

/* ── Item pickers follow the form's company ──
 *
 * `Item.company` is a Custom Field this app does not own — another app on the
 * site adds it — so every line below is a no-op where it is absent, and the
 * check is made once per session against Item's own meta rather than against a
 * list of sites or apps.
 *
 * Items with no company are shared stock and stay visible everywhere. A blank
 * in the `in` list is what lets them through: Frappe's query builder compares
 * `coalesce(company, '')`, so `''` matches a NULL as well as an empty string.
 *
 * Fields are found from the meta on each form rather than listed here, so a
 * new document or a new Item field is covered the day it is added.
 */

// From modules.txt. Identifies this app's forms — not the fields to filter,
// which are discovered below.
CMS.APP_MODULES = [
    "BOQ Management", "Estimation", "Project Costing", "Site Management",
    "Progress Billing", "Subcontractor Management", "Material Planning",
    "Construction Setup",
];

CMS.itemHasCompany = function () {
    if (!CMS._item_company) {
        // with_doctype resolves immediately once Item's meta is in locals.
        CMS._item_company = frappe.model
            .with_doctype("Item")
            .then(() => Boolean(frappe.meta.has_field("Item", "company")));
    }
    return CMS._item_company;
};

/* ── …and say whether they are asking for work or for material ──
 *
 * Both come out of the same Item master, and on a material row they sit side
 * by side — `item_code` is the cement, `work_item` is the plastering it is
 * for. A line of WORK is a service item (Maintain Stock off): the thing the
 * job builds, priced by a Rate Analysis. MATERIAL is a stock item: the thing
 * a store issues. Offering either list where the other belongs is how cement
 * ends up attributed to cement, and how a bill gets priced against something
 * that has no analysis behind it.
 *
 * Keyed by "DocType.fieldname" for the exceptions; anything called
 * `work_item` is work wherever it appears, so new documents are covered
 * without being listed.
 */
CMS.ITEM_KIND = {
    // Lines of work — what the job builds and what it is paid for.
    "BOQ Item.item_code": "work",
    "BOQ Template Item.item_code": "work",
    "Variation Order Item.item_code": "work",
    "Cost Estimation Item.item_code": "work",
    "Rate Analysis.item_code": "work",
    "IPC Item.item_code": "work",
    "Subcontract Item.item_code": "work",
    "Subcontractor Work Order Item.item_code": "work",
    "Subcontractor Payment Item.item_code": "work",
    // Material — what a store issues against that work.
    "Material Consumption Item.item_code": "material",
    "Material Forecast Item.item_code": "material",
    "Site Transfer Item.item_code": "material",
    "Site Material Request Item.item_code": "material",
    "Site Report Material.item_code": "material",
};

CMS.itemKind = function (doctype, fieldname, row) {
    if (fieldname === "work_item" || fieldname === "cms_work_item") return "work";
    // A resource is material only when the row says it is; labour, plant and
    // subcontract legitimately name a service item or none at all.
    if (doctype === "Rate Analysis Resource" && fieldname === "resource_item") {
        return row && row.resource_type === "Material" ? "material" : null;
    }
    return CMS.ITEM_KIND[`${doctype}.${fieldname}`] || null;
};

/**
 * The scope for one Item picker: the form's company, and the kind of item the
 * field is asking for. Read at search time, so a row's own type is current.
 */
CMS.itemQuery = function (doctype, fieldname) {
    return function (doc, cdt, cdn) {
        const filters = [];
        // Nothing to scope to — a template or a settings page has no company.
        if (CMS._itemScoped && doc && doc.company) {
            filters.push(["company", "in", [doc.company, ""]]);
        }
        const kind = CMS.itemKind(doctype, fieldname, cdt ? locals[cdt][cdn] : null);
        if (kind === "work") filters.push(["is_stock_item", "=", 0]);
        if (kind === "material") filters.push(["is_stock_item", "=", 1]);
        return { filters };
    };
};

/** Kept for callers outside this file. */
CMS.itemCompanyFilter = function (doc) {
    if (!doc || !doc.company) return {};
    return { filters: [["company", "in", [doc.company, ""]]] };
};

/** Point every Item link on this form, and in its grids, at the same scope. */
CMS.scopeItemPickers = function (frm) {
    if (!frm || !frm.meta || !CMS.APP_MODULES.includes(frm.meta.module)) return;
    const fields = frm.meta.fields || [];

    CMS.itemHasCompany().then((scoped) => {
        CMS._itemScoped = scoped;
        fields.forEach((df) => {
            if (df.fieldtype === "Link" && df.options === "Item") {
                frm.set_query(df.fieldname, CMS.itemQuery(frm.doctype, df.fieldname));
            } else if (df.fieldtype === "Table" && frm.fields_dict[df.fieldname]) {
                const child = frappe.get_meta(df.options);
                ((child && child.fields) || []).forEach((cdf) => {
                    if (cdf.fieldtype === "Link" && cdf.options === "Item") {
                        frm.set_query(cdf.fieldname, df.fieldname,
                                      CMS.itemQuery(df.options, cdf.fieldname));
                    }
                });
            }
        });
    });
};

// Every form, without a handler per doctype: form.js triggers this on render.
$(document).on("form-refresh", (e, frm) => CMS.scopeItemPickers(frm));

/**
 * Offer only the batches of this row's item that the store actually holds.
 *
 * The pickers were plain Links: every batch on the site, of every item, in
 * every warehouse. ERPNext's own query reads the stock ledger and answers the
 * question properly, so use that rather than a second, worse version of it.
 */
CMS.batchQuery = function (frm, tablefield, warehouseField) {
    frm.set_query("batch_no", tablefield, (doc, cdt, cdn) => {
        const row = locals[cdt][cdn];
        return {
            query: "erpnext.controllers.queries.get_batch_no",
            filters: {
                item_code: row.item_code,
                warehouse: row.warehouse || doc[warehouseField] || null,
                posting_date: doc.posting_date || doc.transfer_date || doc.report_date,
                include_expired_batches: 1,
            },
        };
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
        // Reported, not applied — see BOQ.calculate_item_amounts.
        r.margin_percent = flt(r.cost_rate)
            ? (flt(r.rate) - flt(r.cost_rate)) / flt(r.cost_rate) * 100 : 0;
        r.margin_amount = (flt(r.rate) - flt(r.cost_rate)) * flt(r.qty);
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
    // The tender sum is the sum of the priced lines — see BOQ.calculate_totals.
    doc.grand_total = flt(doc.total_amount);
};

CMS.calc["Retention Release"] = function (doc) {
    // released_to_date is server-owned (it reads other releases), so the form
    // is handed it and mirrors the arithmetic from there. Without it the form
    // showed held - this_release and the server stored held - released - this.
    doc.balance_retention = flt(doc.total_retention_held)
        - flt(doc.released_to_date) - flt(doc.release_amount);

    let running = flt(doc.release_amount);
    (doc.taxes || []).forEach((r) => {
        let amount = 0;
        if (r.charge_type === "Actual") amount = flt(r.tax_amount);
        else if (r.charge_type === "On Net Total") amount = flt(doc.release_amount) * flt(r.rate) / 100;
        else if (r.charge_type === "On Previous Row Amount")
            amount = flt((doc.taxes[cint(r.row_id) - 1] || {}).tax_amount) * flt(r.rate) / 100;
        else if (r.charge_type === "On Previous Row Total")
            amount = flt((doc.taxes[cint(r.row_id) - 1] || {}).total) * flt(r.rate) / 100;
        r.tax_amount = amount;
        running += amount;
        r.total = running;
    });
    doc.total_taxes_and_charges = (doc.taxes || []).reduce((t, r) => t + flt(r.tax_amount), 0);
    doc.total_payable = flt(doc.release_amount) + flt(doc.total_taxes_and_charges);
};

CMS.calc["Rate Analysis"] = function (doc) {
    const t = { Material: 0, Labour: 0, Equipment: 0, Subcontract: 0, Overhead: 0 };
    (doc.resources || []).forEach(r => {
        r.amount = flt(r.qty) * flt(r.rate);
        // Waste belongs in the quantity, put there by whoever measured it.
        if (r.resource_type in t) t[r.resource_type] += flt(r.amount);
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
        r.remaining_qty = flt(r.contract_qty) - flt(r.cumulative_qty);
        r.percent_complete = flt(r.contract_amount) > 0
            ? flt(r.cumulative_amount) / flt(r.contract_amount) * 100 : 0;
        gross += flt(r.amount_this_period);
    });
    doc.gross_amount_this_period = gross;
    doc.cumulative_amount_to_date = flt(doc.previous_cumulative_amount) + gross;
    doc.retention_amount = flt(doc.gross_amount_this_period) * flt(doc.retention_percent) / 100;
    doc.net_payable_this_period = flt(doc.gross_amount_this_period)
        - flt(doc.retention_amount) - flt(doc.advance_recovery_amount) - flt(doc.other_deductions);

    // Charged on the NET payable — the same base the invoice is raised for, so
    // the certificate and the invoice cannot reach different figures.
    // Mirrors InterimPaymentCertificate.calculate_taxes.
    let running = flt(doc.net_payable_this_period);
    (doc.taxes || []).forEach((r, i) => {
        let amount = 0;
        if (r.charge_type === "Actual") {
            amount = flt(r.tax_amount);
        } else if (r.charge_type === "On Net Total") {
            amount = flt(doc.net_payable_this_period) * flt(r.rate) / 100;
        } else if (r.charge_type === "On Previous Row Amount") {
            amount = flt((doc.taxes[cint(r.row_id) - 1] || {}).tax_amount) * flt(r.rate) / 100;
        } else if (r.charge_type === "On Previous Row Total") {
            amount = flt((doc.taxes[cint(r.row_id) - 1] || {}).total) * flt(r.rate) / 100;
        }
        r.tax_amount = amount;
        running += amount;
        r.total = running;
    });
    doc.total_taxes_and_charges = (doc.taxes || []).reduce((t, r) => t + flt(r.tax_amount), 0);
    doc.total_payable = flt(doc.net_payable_this_period) + flt(doc.total_taxes_and_charges);
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
    // The priced schedule is the value — see SubcontractAgreement.calculate_items.
    if ((doc.items || []).length) {
        doc.subcontract_value = doc.items.reduce((t, r) => t + flt(r.amount), 0);
    }
    doc.advance_amount = flt(doc.subcontract_value) * flt(doc.advance_percent) / 100;
};

CMS.calc["Subcontractor Payment Certificate"] = function (doc) {
    doc.gross_amount_claimed = (doc.items || []).reduce((t, r) => t + flt(r.amount_claimed), 0);
    // A certificate certifies what was claimed unless the engineer reduces it —
    // see SubcontractorPaymentCertificate.calculate_totals.
    if (!flt(doc.certified_amount)) doc.certified_amount = flt(doc.gross_amount_claimed);
    doc.retention_deduction = flt(doc.certified_amount) * flt(doc.retention_percent) / 100;
    doc.net_payable = flt(doc.certified_amount) - flt(doc.retention_deduction)
        - flt(doc.advance_recovery) - flt(doc.other_deductions);

    let running = flt(doc.net_payable);
    (doc.taxes || []).forEach((r) => {
        let amount = 0;
        if (r.charge_type === "Actual") amount = flt(r.tax_amount);
        else if (r.charge_type === "On Net Total") amount = flt(doc.net_payable) * flt(r.rate) / 100;
        else if (r.charge_type === "On Previous Row Amount")
            amount = flt((doc.taxes[cint(r.row_id) - 1] || {}).tax_amount) * flt(r.rate) / 100;
        else if (r.charge_type === "On Previous Row Total")
            amount = flt((doc.taxes[cint(r.row_id) - 1] || {}).total) * flt(r.rate) / 100;
        r.tax_amount = amount;
        running += amount;
        r.total = running;
    });
    doc.total_taxes_and_charges = (doc.taxes || []).reduce((t, r) => t + flt(r.tax_amount), 0);
    doc.total_payable = flt(doc.net_payable) + flt(doc.total_taxes_and_charges);
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
        // Rounded UP for a whole-number UOM, never down: ordering 8,059 of the
        // 8,059.8 a take-off asks for leaves the job short by design.
        const outstanding = Math.max(0, flt(r.net_qty_required) - flt(r.already_ordered_qty));
        r.qty_to_order = cint(r.uom_must_be_whole) && outstanding > 0
            ? Math.ceil(outstanding - 0.000001) : outstanding;
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
            : {
                  item_code: item,
                  company: frm.doc.company,
                  // Only sent when the caller has a basis; absent means the
                  // existing preference chain, exactly as before.
                  basis: opts.basis && opts.basis !== "Manual" ? opts.basis : undefined,
                  price_list: opts.priceList || undefined,
              },
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

/**
 * Show the rate build-up a line was priced from, exactly as it stood then,
 * beside the library's current figures so any movement is obvious.
 */
CMS.showRateBuildUp = function (frm) {
    const priced = (frm.doc.items || []).filter(r => r.rate_build_up || r.rate_analysis_ref);
    if (!priced.length) {
        frappe.msgprint(__("No line on this document was priced from a Rate Analysis."));
        return;
    }
    const pick = new frappe.ui.Dialog({
        title: __("Rate Build-up"),
        fields: [{
            fieldname: "idx", label: __("Line"), fieldtype: "Select", reqd: 1,
            options: priced.map(r => `${r.idx} — ${r.item_code || r.description || ""}`),
        }],
        primary_action_label: __("Show"),
        primary_action(values) {
            pick.hide();
            const idx = values.idx.split(" — ")[0];
            frappe.call({
                method: "construction_management_suite.api.boq.get_rate_build_up",
                args: { doctype: frm.doc.doctype, docname: frm.doc.name, idx: idx },
                freeze: true,
                callback: (r) => render_build_up(frm, r.message),
            });
        },
    });
    pick.show();
};

function render_build_up(frm, data) {
    if (!data) return;
    const cur = frm.doc.currency;
    const money = (v) => format_currency(flt(v), cur);

    if (!data.frozen && !data.current) {
        frappe.msgprint({ title: __("Rate Build-up"), message: data.message, indicator: "orange" });
        return;
    }

    // No frozen copy, but the analysis still exists — show it as today's, with
    // the warning first so nobody reads it as the original.
    const f = data.frozen || data.current;
    const asOfToday = !data.frozen;
    const c = data.current;
    const moved = c && Math.abs(flt(c.rate_per_unit) - flt(f.rate_per_unit)) > 0.005;

    let html = `<div style="font-size:13px">`;
    if (asOfToday) {
        html += `<div class="alert alert-warning" style="font-size:12.5px">${data.message}</div>`;
    }
    html += `
      <p><b>${frappe.utils.escape_html(f.analysis_name || "")}</b>
         <span class="text-muted">${f.rate_analysis}</span><br>
         <span class="text-muted">${asOfToday
             ? __("As it stands today")
             : __("As applied on") + " " + frappe.datetime.str_to_user(f.applied_on)}</span></p>
      <table class="table table-bordered table-sm">
        <thead><tr>
          <th>${__("Type")}</th><th>${__("Resource")}</th><th>${__("UOM")}</th>
          <th class="text-right">${__("Qty")}</th><th class="text-right">${__("Rate")}</th>
          <th class="text-right">${__("Waste")}</th><th class="text-right">${__("Net")}</th>
        </tr></thead><tbody>`;
    (f.resources || []).forEach(r => {
        html += `<tr><td>${r.type || ""}</td><td>${frappe.utils.escape_html(r.description || "")}</td>
          <td>${r.uom || ""}</td><td class="text-right">${r.qty}</td>
          <td class="text-right">${money(r.rate)}</td>
          <td class="text-right">${money(r.amount || r.net_amount)}</td></tr>`;
    });
    html += `</tbody><tfoot>`;
    Object.entries(f.buckets || {}).forEach(([k, v]) => {
        if (flt(v)) html += `<tr><td colspan="6" class="text-right text-muted">${k}</td>
                             <td class="text-right">${money(v)}</td></tr>`;
    });
    html += `<tr><td colspan="6" class="text-right"><b>${__("Rate per unit")}</b>
             ${flt(f.output_qty) !== 1 ? `<span class="text-muted">(${__("for")} ${f.output_qty})</span>` : ""}</td>
             <td class="text-right"><b>${money(f.rate_per_unit)}</b></td></tr>`;
    html += `</tfoot></table>`;

    if (asOfToday && flt(data.line_cost_rate)) {
        html += `<div class="text-muted" style="font-size:12px">${
            __("This line's cost rate is {0}.", [money(data.line_cost_rate)])}</div>`;
    } else if (moved) {
        html += `<div class="alert alert-warning" style="font-size:12.5px">
          ${__("The library has changed since. This analysis now prices at <b>{0}</b>, against <b>{1}</b> when this line was set. The line keeps what it was priced at.",
               [money(c.rate_per_unit), money(f.rate_per_unit)])}</div>`;
    } else if (c) {
        html += `<div class="text-muted" style="font-size:12px">${__("The library still prices this the same.")}</div>`;
    }
    html += `</div>`;

    new frappe.ui.Dialog({ title: __("Rate Build-up"), size: "large",
                           fields: [{ fieldtype: "HTML", options: html }] }).show();
}

/* ══════════════════ Work breakdown ══════════════════
 * A bill or an estimate at all three of its levels at once: the section, the
 * work under it, and what that work is priced to consume. The grid shows one
 * level and the build-up dialog shows another one line at a time, so until now
 * the view a quantity surveyor actually reads could only be had by exporting.
 *
 * Read from the saved document, not from the grid in front of you — the
 * resources come from the frozen build-up on each line, which only exists once
 * the line has been saved. Hence the reminder and the refresh.
 */

CMS.renderWorkBreakdown = function (frm, fieldname) {
    const field = frm.fields_dict[fieldname || "breakdown_html"];
    if (!field) return;
    const $wrapper = field.$wrapper.empty();

    if (frm.is_new()) {
        $wrapper.html(`<div class="text-muted">${__("Save the document to see its breakdown.")}</div>`);
        return;
    }
    $wrapper.html(`<div class="text-muted">${__("Loading…")}</div>`);
    frappe.call({
        method: "construction_management_suite.api.boq.get_work_breakdown",
        args: { doctype: frm.doctype, docname: frm.doc.name },
        callback: (r) => {
            if (!r.message) return;
            $wrapper.html(CMS.workBreakdownHTML(r.message));
            bind_breakdown(frm, $wrapper);
        },
    });
};

CMS.workBreakdownHTML = function (data) {
    const cur = data.currency;
    const money = (v) => format_currency(flt(v), cur);
    const num = (v, p) => format_number(flt(v), null, p === undefined ? 3 : p);
    const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
    const cost = data.shows_selling;   // a bill has a selling side AND a cost side
    const span = cost ? 7 : 6;
    const titleSpan = span - (cost ? 2 : 1);

    if (!data.sections.length) {
        return `<div class="text-muted">${__("No lines on this document yet.")}</div>`;
    }

    const rows = [];
    data.sections.forEach((section, si) => {
        rows.push(`
            <tr class="cms-wb-section">
                <td colspan="${titleSpan}">${esc(section.title)}</td>
                <td class="cms-wb-num">${money(section.amount)}</td>
                ${cost ? `<td class="cms-wb-num">${money(section.cost_amount)}</td>` : ""}
            </tr>`);

        section.lines.forEach((line, li) => {
            const key = `${si}-${li}`;
            const priced = line.resources.length;
            rows.push(`
                <tr class="cms-wb-work" data-key="${key}">
                    <td>
                        <span class="cms-wb-toggle">${priced ? "▸" : "&nbsp;"}</span>
                        <b>${esc(line.item_no || line.item_code || "")}</b>
                    </td>
                    <td>
                        ${esc(line.item_no ? line.item_code : "")}
                        ${line.item_no && line.item_code ? " — " : ""}
                        ${esc(line.description || "")}
                        ${line.source === "Live analysis"
                            ? `<span class="cms-wb-tag">${__("live rate")}</span>` : ""}
                        ${line.source === "No analysis"
                            ? `<span class="cms-wb-tag warn">${__("no analysis")}</span>` : ""}
                    </td>
                    <td>${esc(line.uom || "")}</td>
                    <td class="cms-wb-num">${num(line.qty)}</td>
                    <td class="cms-wb-num">${money(line.rate)}</td>
                    <td class="cms-wb-num">${money(line.amount)}</td>
                    ${cost ? `<td class="cms-wb-num">${money(line.cost_amount)}</td>` : ""}
                </tr>`);

            line.resources.forEach(res => {
                rows.push(`
                    <tr class="cms-wb-res" data-parent="${key}" hidden>
                        <td class="cms-wb-type">${esc(res.type || "")}</td>
                        <td class="cms-wb-indent">
                            ${esc(res.item || "")}${res.item && res.description ? " — " : ""}
                            <span class="text-muted">${esc(res.description || "")}</span>
                        </td>
                        <td>${esc(res.uom || "")}</td>
                        <td class="cms-wb-num">
                            ${num(res.total_qty)}
                            <div class="cms-wb-per">${num(res.qty_per_unit, 4)} / ${esc(line.uom || __("unit"))}</div>
                        </td>
                        <td class="cms-wb-num">${money(res.rate)}</td>
                        <td class="cms-wb-num">${money(res.amount)}</td>
                        ${cost ? "<td></td>" : ""}
                    </tr>`);
            });
        });
    });

    return `
        <div class="cms-wb">
            <div class="cms-wb-bar">
                <a class="cms-wb-all" data-open="1">${__("Expand all")}</a>
                <span class="text-muted">${__("As saved — resources come from each line's frozen build-up.")}</span>
            </div>
            <table class="cms-wb-table">
                <thead>
                    <tr>
                        <th style="width:12%">${__("Item")}</th>
                        <th>${__("Description")}</th>
                        <th style="width:7%">${__("UOM")}</th>
                        <th style="width:12%" class="cms-wb-num">${__("Qty")}</th>
                        <th style="width:12%" class="cms-wb-num">${__("Rate")}</th>
                        <th style="width:14%" class="cms-wb-num">${cost ? __("Amount") : __("Cost")}</th>
                        ${cost ? `<th style="width:14%" class="cms-wb-num">${__("Cost")}</th>` : ""}
                    </tr>
                </thead>
                <tbody>${rows.join("")}</tbody>
                <tfoot>
                    <tr>
                        <td colspan="${titleSpan}">${__("Total")}</td>
                        <td class="cms-wb-num">${money(data.totals.amount)}</td>
                        ${cost ? `<td class="cms-wb-num">${money(data.totals.cost_amount)}</td>` : ""}
                    </tr>
                </tfoot>
            </table>
        </div>`;
};

/** One work line's resources, or all of them. */
function bind_breakdown(frm, $wrapper) {
    $wrapper.on("click", ".cms-wb-work", function () {
        const key = $(this).data("key");
        const open = $(this).hasClass("open");
        $(this).toggleClass("open", !open).find(".cms-wb-toggle").text(open ? "▸" : "▾");
        $wrapper.find(`.cms-wb-res[data-parent="${key}"]`).prop("hidden", open);
    });
    $wrapper.on("click", ".cms-wb-all", function () {
        const open = Number($(this).data("open"));
        $(this).data("open", open ? 0 : 1).text(open ? __("Collapse all") : __("Expand all"));
        $wrapper.find(".cms-wb-work").toggleClass("open", Boolean(open))
            .find(".cms-wb-toggle").text(open ? "▾" : "▸");
        $wrapper.find(".cms-wb-res").prop("hidden", !open);
    });
}
