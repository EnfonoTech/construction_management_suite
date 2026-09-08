import frappe
from frappe.model.document import Document
from frappe.utils import flt
from construction_management_suite.utils.validations import validate_project_company


class SubcontractorWorkOrder(Document):
    def validate(self):
        validate_project_company(self)
        self.calculate_totals()

    def calculate_totals(self):
        total_contract = 0
        total_completed = 0
        for item in self.items:
            item.contract_amount = flt(item.contract_qty) * flt(item.contract_rate)
            item.completed_amount = flt(item.completed_qty) * flt(item.contract_rate)
            if flt(item.contract_qty) > 0:
                item.completion_percent = flt(item.completed_qty) / flt(item.contract_qty) * 100
            total_contract += flt(item.contract_amount)
            total_completed += flt(item.completed_amount)
        self.total_contract_value = total_contract
        self.total_completed_value = total_completed
        if flt(total_contract) > 0:
            self.completion_percent = flt(total_completed) / flt(total_contract) * 100
