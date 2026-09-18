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

    def before_submit(self):
        self.submitted_by = frappe.session.user
        self.status = "Submitted"

    def before_cancel(self):
        # before, not on_cancel: on_cancel runs after the row is written.
        self.status = "Cancelled"

    def on_submit(self):
        self._create_project_budget()

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
        if flt(self.selling_price) > 0:
            self.margin_percent = (flt(self.selling_price) - flt(self.total_estimated_cost)) / flt(self.selling_price) * 100

    def _create_project_budget(self):
        """On approval, seed a Project Budget from this estimation."""
        if not self.project:
            return
        if frappe.db.exists("Project Budget", {"project": self.project, "docstatus": ["!=", 2]}):
            frappe.msgprint(_("A Project Budget already exists for this project. Estimation submitted."))
            return
        budget = frappe.new_doc("Project Budget")
        budget.project = self.project
        budget.company = self.company
        budget.currency = self.currency
        budget.budget_title = f"Budget from {self.name}"
        budget.cost_estimation_ref = self.name
        budget.total_budget = self.total_estimated_cost
        for item in self.items:
            budget.append("items", {
                "cost_head": item.description or item.item_code,
                "budgeted_amount": item.total_cost,
            })
        budget.insert(ignore_permissions=True)
        frappe.msgprint(_("Project Budget {0} created from this estimation").format(budget.name))
