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

/* ── Form-level Shortcuts ── */
frappe.ui.form.on("Project", {
    refresh(frm) {
        if (frm.doc.name && !frm.is_new()) {
            frm.add_custom_button(__("View BOQs"), () => {
                frappe.set_route("List", "BOQ", { project: frm.doc.name });
            }, __("CMS"));
            frm.add_custom_button(__("Project Budget"), () => {
                frappe.set_route("List", "Project Budget", { project: frm.doc.name });
            }, __("CMS"));
            frm.add_custom_button(__("Daily Reports"), () => {
                frappe.set_route("List", "Daily Site Report", { project: frm.doc.name });
            }, __("CMS"));
            frm.add_custom_button(__("Cost Dashboard"), () => {
                CMS.getProjectDashboard(frm.doc.name, (data) => {
                    if (!data) return;
                    const dialog = new frappe.ui.Dialog({
                        title: __("Project Cost Dashboard — {0}", [frm.doc.name]),
                        size: "large",
                    });
                    const $body = $(dialog.body);
                    $body.css({ display: "flex", flexWrap: "wrap", padding: "16px" });
                    const b = data.budget || {};
                    CMS.renderKPICard($body, __("Total Budget"), b.total_budget, frm.doc.currency, "");
                    CMS.renderKPICard($body, __("Actual Cost"), b.total_actual_cost, frm.doc.currency,
                        (b.budget_utilization_percent || 0) > 90 ? "over-budget" : "on-track");
                    CMS.renderKPICard($body, __("Total Billed"), (data.billing || {}).total_billed, frm.doc.currency, "");
                    dialog.show();
                });
            }, __("CMS"));
        }
    },
});

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
