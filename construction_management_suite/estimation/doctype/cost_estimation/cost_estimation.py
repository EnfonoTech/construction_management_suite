import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt
from construction_management_suite.utils.settings import cms_setting
from construction_management_suite.utils.validations import validate_project_company
from construction_management_suite.utils.titles import (
    month_of,
    project_label,
    set_auto_title,
)


class CostEstimation(Document):
    def validate(self):
        set_auto_title(self, "estimation_title", [_("Estimate"), project_label(self.project), month_of(self.estimation_date)])
        validate_project_company(self)
        self.set_missing_defaults()
        self.pull_costs_from_rate_analysis()
        self.calculate_totals()

    def validate_model(self):
        """Every line must be a line of work, and something must be able to cost it."""
        from construction_management_suite.utils.validations import (
            validate_one_estimate_per_project,
            validate_rate_analysis_company,
            validate_rate_analysis_present,
            validate_work_item,
        )

        validate_one_estimate_per_project(self)
        for row in self.items:
            validate_work_item(row.item_code, row.idx)
        validate_rate_analysis_company(self)
        validate_rate_analysis_present(self.items, company=self.company)

    def before_submit(self):
        self.validate_model()
        self.submitted_by = frappe.session.user
        self.status = "Submitted"

    def before_cancel(self):
        # before, not on_cancel: on_cancel runs after the row is written.
        self.status = "Cancelled"

    def on_submit(self):
        self._create_project_budget()

    # Recomputed and persisted when a line is priced after approval.
    ROW_FIELDS = (
        "material_cost", "labour_cost", "equipment_cost", "subcontract_cost",
        "overhead_cost", "unit_cost", "total_cost", "rate_build_up", "rate_applied_on",
    )
    DOC_FIELDS = (
        "estimated_material_cost", "estimated_labour_cost", "estimated_equipment_cost",
        "estimated_subcontract_cost", "estimated_overhead_cost", "contingency_amount",
        "total_estimated_cost", "margin_percent",
    )

    def before_update_after_submit(self):
        """The analysis may change after approval; the scope may not."""
        on_file = frappe.get_all(
            "Cost Estimation Item",
            filters={"parent": self.name, "parenttype": self.doctype},
            fields=["name", "rate_analysis_ref"],
        )
        if {r.name for r in on_file} != {i.name for i in self.items}:
            frappe.throw(
                _("Lines cannot be added or removed once the estimate is approved. "
                  "Only the Rate Analysis on a line already here can be set."),
                title=_("Approved estimate"),
            )
        self.guard_pricing_after_approval({r.name: r.rate_analysis_ref for r in on_file})

    def guard_pricing_after_approval(self, stored):
        """Pricing an approved estimate is off unless this one says otherwise.

        Off by default, and per estimate rather than per site: re-pricing an
        approved document is a deliberate act on that document, and a site-wide
        switch would either block the estimate that needs it or open every
        estimate that does not.
        """
        if self.allow_pricing_after_approval:
            return
        changed = [
            item for item in self.items
            if (item.rate_analysis_ref or None) != (stored.get(item.name) or None)
        ]
        if not changed:
            return
        frappe.throw(
            _("An administrator has to allow pricing on this approved estimate "
              "before a Rate Analysis can be set on row {0}.")
            .format(", ".join(str(i.idx) for i in changed[:5])),
            title=_("Approved estimate"),
        )

    def on_update_after_submit(self):
        """Price a line from the library after the estimate is approved.

        Most lines are costed by hand, and the take-off cannot see one until an
        analysis says what the work consumes — so an approved estimate would
        otherwise plan no material at all. The analysis stays editable and the
        line re-costs itself here.

        The Project Budget is left alone on purpose. It was approved at its own
        figure, which normally sits at or above the estimate, and moving it
        would change an approved number without anyone approving it.
        """
        self.drop_stale_build_ups()
        self.pull_costs_from_rate_analysis()
        self.calculate_totals()
        self.persist_after_submit()
        frappe.msgprint(
            _("Re-costed from the rate library. The Project Budget is unchanged."),
            alert=True,
        )

    def drop_stale_build_ups(self):
        """A line whose analysis was swapped needs its reasoning frozen again."""
        for item in self.items:
            if not (item.rate_analysis_ref and item.rate_build_up):
                continue
            try:
                frozen = json.loads(item.rate_build_up)
            except (ValueError, TypeError):
                frozen = {}
            if frozen.get("rate_analysis") != item.rate_analysis_ref:
                item.rate_build_up = None

    def persist_after_submit(self):
        """update_after_submit writes only what the client sent.

        Everything derived from the analysis — the five components, the unit
        cost, the document totals — has to be written explicitly or the figures
        on screen are not the figures in the table.
        """
        for item in self.items:
            for field in self.ROW_FIELDS:
                item.db_set(field, item.get(field), update_modified=False)
        for field in self.DOC_FIELDS:
            self.db_set(field, self.get(field), update_modified=False)

    def set_missing_defaults(self):
        """Contingency from the module default when this estimate states none.

        The docfield carried a hardcoded 5, which _set_defaults applies before
        validate ever runs — so the setting could never be reached.
        """
        if not flt(self.contingency_percent):
            self.contingency_percent = flt(cms_setting("default_contingency_percent", 0))

    def pull_costs_from_rate_analysis(self):
        for item in self.items:
            if item.rate_analysis_ref:
                ra = frappe.get_cached_doc("Rate Analysis", item.rate_analysis_ref)
                # The analysis totals cover output_qty units, not one — divide, or a
                # rate built for a 10 m³ batch reports ten times its true cost.
                output = flt(ra.output_qty) or 1
                item.material_cost = flt(ra.total_material_cost) / output
                item.labour_cost = flt(ra.total_labour_cost) / output
                item.equipment_cost = flt(ra.total_equipment_cost) / output
                item.subcontract_cost = flt(ra.total_subcontract_cost) / output
                item.overhead_cost = flt(ra.total_overhead_cost) / output
                item.unit_cost = flt(ra.rate_per_unit)
                if not item.rate_build_up:
                    # Freeze the reasoning the first time only; re-saving an
                    # estimate must not quietly restate history.
                    from construction_management_suite.api.boq import snapshot_rate_analysis

                    item.rate_build_up = snapshot_rate_analysis(ra)
                    item.rate_applied_on = frappe.utils.now()
            else:
                item.unit_cost = (
                    flt(item.material_cost) + flt(item.labour_cost) + flt(item.equipment_cost)
                    + flt(item.subcontract_cost) + flt(item.overhead_cost)
                )
            item.total_cost = flt(item.qty) * flt(item.unit_cost)

    def calculate_totals(self):
        self.estimated_material_cost = sum(flt(i.material_cost) * flt(i.qty) for i in self.items)
        self.estimated_labour_cost = sum(flt(i.labour_cost) * flt(i.qty) for i in self.items)
        self.estimated_equipment_cost = sum(flt(i.equipment_cost) * flt(i.qty) for i in self.items)
        self.estimated_subcontract_cost = sum(flt(i.subcontract_cost) * flt(i.qty) for i in self.items)
        self.estimated_overhead_cost = sum(flt(i.overhead_cost) * flt(i.qty) for i in self.items)
        subtotal = sum(flt(i.total_cost) for i in self.items)
        self.contingency_amount = flt(subtotal) * flt(self.contingency_percent) / 100
        self.total_estimated_cost = subtotal + flt(self.contingency_amount)
        # Always written, never left at whatever arrived: the field is
        # allow_on_submit so the client can send one, and only the server's
        # figure should survive.
        self.margin_percent = (
            (flt(self.selling_price) - flt(self.total_estimated_cost)) / flt(self.selling_price) * 100
            if flt(self.selling_price) > 0 else 0
        )

    def _create_project_budget(self):
        """On approval, seed a Project Budget — or bring the draft one up to date.

        An estimate is revised by cancelling and amending it, so the budget has
        to follow it. This returned the moment any budget existed, which meant
        every revision from then on left the budget stating the superseded
        figure — and the variance report, the project cockpit and the estimate's
        own headline all read that figure.

        A submitted budget is not rewritten behind anyone's back: it is a
        commitment somebody approved, and ERPNext's amend flow is how it moves.
        Say so, and leave it to them.
        """
        if not self.project:
            return

        existing = frappe.db.get_value(
            "Project Budget",
            {"project": self.project, "docstatus": ("!=", 2)},
            ["name", "docstatus"],
            as_dict=True,
        )
        if existing and existing.docstatus == 1:
            frappe.msgprint(
                _(
                    "{0} is submitted and still states the previous estimate. "
                    "Amend it and use <b>Refresh from Estimate</b> to bring it "
                    "onto this one."
                ).format(frappe.utils.get_link_to_form("Project Budget", existing.name)),
                title=_("Budget not updated"),
                indicator="orange",
            )
            return

        budget = (
            frappe.get_doc("Project Budget", existing.name)
            if existing
            else frappe.new_doc("Project Budget")
        )
        self.fill_project_budget(budget)
        budget.flags.ignore_permissions = True
        budget.save()
        frappe.msgprint(
            _("Project Budget {0} updated from this estimation").format(budget.name)
            if existing
            else _("Project Budget {0} created from this estimation").format(budget.name)
        )

    def fill_project_budget(self, budget):
        """Write this estimate's cost plan onto a draft budget.

        The scope is the estimate's, so the rows are rewritten rather than
        merged — but a cost code somebody put against a head is theirs, and is
        carried over wherever that head survives the revision.
        """
        coded = {r.cost_head: r.cost_code for r in (budget.get("items") or []) if r.cost_code}

        budget.project = self.project
        budget.company = self.company
        budget.currency = self.currency
        budget.cost_estimation_ref = self.name
        budget.total_budget = self.total_estimated_cost
        budget.set("items", [])
        for item in self.items:
            head = item.description or item.item_code
            budget.append("items", {
                "cost_head": head,
                "cost_code": coded.get(head),
                "budgeted_amount": item.total_cost,
            })
        return budget
