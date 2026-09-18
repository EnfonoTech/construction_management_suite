import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt
from construction_management_suite.utils.validations import validate_project_company
from construction_management_suite.utils.titles import (
    month_of,
    project_label,
    set_auto_title,
)


class SubcontractorWorkOrder(Document):
    def before_cancel(self):
        # before, not on_cancel: on_cancel runs after the row is written.
        self.status = "Cancelled"

    def validate(self):
        set_auto_title(self, "work_order_title", [_("Work Order"), self.subcontractor, project_label(self.project)])
        validate_project_company(self)
        self.calculate_totals()

    @frappe.whitelist()
    def get_scope_from_agreement(self):
        """Pull the part of the agreement not yet instructed."""
        from construction_management_suite.api.boq import get_agreement_lines

        if not self.subcontract_agreement:
            frappe.throw(_("Choose the agreement this order releases"))
        existing = {i.agreement_item_ref: i for i in self.items if i.agreement_item_ref}
        added = topped = 0
        for line in get_agreement_lines(self.subcontract_agreement, work_order=self.name):
            for key in ("_agreed_qty", "_instructed"):
                line.pop(key, None)
            row = existing.get(line["agreement_item_ref"])
            if row:
                # The remaining quantity is what get_agreement_lines returned,
                # already net of what is on this order — so add it rather than
                # reporting the agreement as fully instructed.
                row.contract_qty = flt(row.contract_qty) + flt(line["contract_qty"])
                topped += 1
            else:
                self.append("items", line)
                added += 1
        return {"added": added, "topped_up": topped}

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
