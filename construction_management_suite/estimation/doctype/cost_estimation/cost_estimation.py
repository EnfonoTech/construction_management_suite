import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt
from construction_management_suite.utils.validations import validate_project_company


class CostEstimation(Document):
    def validate(self):
        validate_project_company(self)
        self.pull_costs_from_rate_analysis()
        self.calculate_totals()

    def before_submit(self):
        self.submitted_by = frappe.session.user
        self.status = "Submitted"

    def on_submit(self):
        self._create_project_budget()

    def pull_costs_from_rate_analysis(self):
        for item in self.items:
            if item.rate_analysis_ref:
                ra = frappe.get_cached_doc("Rate Analysis", item.rate_analysis_ref)
                item.material_cost = flt(ra.total_material_cost)
                item.labour_cost = flt(ra.total_labour_cost)
                item.equipment_cost = flt(ra.total_equipment_cost)
                item.overhead_cost = flt(ra.total_overhead_cost)
                item.unit_cost = flt(ra.rate_per_unit)
            else:
                item.unit_cost = flt(item.material_cost) + flt(item.labour_cost) + flt(item.equipment_cost) + flt(item.overhead_cost)
            item.total_cost = flt(item.qty) * flt(item.unit_cost)

    def calculate_totals(self):
        self.estimated_material_cost = sum(flt(i.material_cost) * flt(i.qty) for i in self.items)
        self.estimated_labour_cost = sum(flt(i.labour_cost) * flt(i.qty) for i in self.items)
        self.estimated_equipment_cost = sum(flt(i.equipment_cost) * flt(i.qty) for i in self.items)
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
        budget.total_budget = self.total_estimated_cost
        for item in self.items:
            budget.append("items", {
                "cost_head": item.description or item.item_code,
                "budgeted_amount": item.total_cost,
            })
        budget.insert(ignore_permissions=True)
        frappe.msgprint(_("Project Budget {0} created from this estimation").format(budget.name))
