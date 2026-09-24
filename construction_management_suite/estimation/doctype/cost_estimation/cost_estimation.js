frappe.ui.form.on("Cost Estimation", {
    refresh(frm) {
        CMS.uomQuery(frm, "items", "item_code");
        CMS.rateAnalysisQuery(frm, "items");
        CMS.renderWorkBreakdown(frm);
        if (!frm.is_new()) frm.add_custom_button(__("Rate Build-up"), () => CMS.showRateBuildUp(frm), __("View"));
        CMS.filterByProject(frm, "boq_ref", { docstatus: 1 });
        CMS.filterProjects(frm);

        // The analysis stays editable after approval so an unpriced line can
        // still reach the take-off; the scope itself must not move.
        if (frm.doc.docstatus === 1 && frm.fields_dict.items) {
            frm.fields_dict.items.grid.cannot_add_rows = true;
            frm.fields_dict.items.grid.cannot_delete_rows = true;
            // Editable only while this estimate says so, so the grid matches
            // what the server will accept.
            frm.fields_dict.items.grid.toggle_enable(
                "rate_analysis_ref", Boolean(frm.doc.allow_pricing_after_approval)
            );
        }

        if (frm.doc.docstatus === 1 && frm.doc.project) {
            frappe.db.get_value("Project Budget",
                { project: frm.doc.project, docstatus: ["<", 2] }, "name"
            ).then(r => {
                if (r.message && r.message.name) {
                    CMS.linkButton(frm, __("Project Budget"), "Project Budget", r.message.name);
                }
            });
            downstream_buttons(frm);
            render_position(frm);
        }
    },

    allow_pricing_after_approval(frm) { frm.refresh(); },

    project(frm) {
        CMS.fillFromProject(frm, { company: "company" });
    },

    company(frm) {
        CMS.filterProjects(frm);
        CMS.clearForeignProject(frm);
        if (frm.doc.company) CMS.currencyFromCompany(frm, frm.doc.company);
    },
    contingency_percent(frm) { CMS.recalc(frm); },
    selling_price(frm) { CMS.recalc(frm); },
    items_remove(frm) { CMS.recalc(frm); },

});

CMS.liveRows("Cost Estimation Item", ["qty", "material_cost", "labour_cost", "equipment_cost", "overhead_cost", "unit_cost"]);


/* The estimate is where the job is bought and built from, so the documents
 * that do that start here. They hang off the project rather than off this
 * document, which is why they are buttons and not a connections panel. */
function downstream_buttons(frm) {
    const make = (label, doctype, extra) => {
        frm.add_custom_button(__(label), () => {
            frappe.new_doc(doctype, Object.assign({
                project: frm.doc.project,
                company: frm.doc.company,
            }, extra || {}));
        }, __("Create"));
    };
    make("Material Forecast", "Material Forecast");
    make("Site Material Request", "Site Material Request");
    make("Material Consumption Entry", "Material Consumption Entry");
    make("Subcontract Agreement", "Subcontract Agreement");

    frm.add_custom_button(__("Material Position"), () => {
        frappe.set_route("query-report", "Material Position", { project: frm.doc.project });
    }, __("View"));
    // The project form is the cockpit — every list filtered to this job is a
    // click away from there, and a route into a child-table list is not.
    CMS.linkButton(frm, __("Project"), "Project", frm.doc.project);
}

/** Where the job stands against the plan this estimate set. */
function render_position(frm) {
    frappe.call({
        method: "construction_management_suite.api.boq.get_estimate_position",
        args: { cost_estimation: frm.doc.name },
        callback: (r) => {
            const d = r.message;
            if (!d) return;
            const money = (v) => format_currency(flt(v), d.currency);
            const cells = [
                [__("Estimated cost"), money(d.estimated)],
                [__("Budget"), d.budget === null
                    ? __("none") : `${money(d.budget)} <span class="text-muted">(${__(d.budget_status)})</span>`],
                [__("On order"), money(d.ordered)],
                [__("Actual"), d.actual === null ? "—" : money(d.actual)],
                [__("Material consumed"), `${money(d.consumed_value)} ${__("of")} ${money(d.planned_value)} ${__("planned")}`],
            ];
            if (d.unpriced) {
                cells.push([__("Unpriced lines"),
                    `<span class="text-danger">${d.unpriced} ${__("of")} ${d.lines}</span>`]);
            }
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
