import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from construction_management_suite.utils.accounting import get_cost_center
from construction_management_suite.utils.validations import validate_project_company


class MaterialConsumptionEntry(Document):
    def validate(self):
        validate_project_company(self)
        for item in self.items:
            item.amount = flt(item.qty) * flt(item.valuation_rate)

    def before_submit(self):
        self.status = "Submitted"

    def on_submit(self):
        self._create_stock_entry()

    def before_cancel(self):
        self.status = "Cancelled"

    def on_cancel(self):
        self._cancel_stock_entry()

    def _cancel_stock_entry(self):
        if not self.stock_entry_ref:
            return
        se = frappe.get_doc("Stock Entry", self.stock_entry_ref)
        if se.docstatus == 1:
            se.cancel()
            frappe.msgprint(_("Stock Entry {0} cancelled").format(se.name))

    def _create_stock_entry(self):
        se = frappe.new_doc("Stock Entry")
        # Site consumption is a plain issue — "…for Manufacture" belongs to Work Orders.
        se.stock_entry_type = "Material Issue"
        se.company = self.company
        se.posting_date = self.posting_date
        se.project = self.project
        cost_center = get_cost_center(self.project, self.company)
        for item in self.items:
            se.append("items", {
                "item_code": item.item_code,
                "qty": flt(item.qty),
                "uom": item.uom,
                "s_warehouse": self.warehouse,
                "batch_no": item.batch_no,
                "cost_center": cost_center,
            })
        se.insert(ignore_permissions=True)
        se.submit()
        self.db_set("stock_entry_ref", se.name)
        frappe.msgprint(_("Stock Entry {0} submitted for material consumption").format(se.name))
