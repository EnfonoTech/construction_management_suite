import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from construction_management_suite.utils.accounting import get_cost_center
from construction_management_suite.utils.validations import validate_project_company


class SiteTransfer(Document):
    def validate(self):
        validate_project_company(self, ("from_project", "to_project"))
        if self.from_warehouse == self.to_warehouse:
            frappe.throw(_("Source and destination warehouses must be different"))

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
        se.stock_entry_type = "Material Transfer"
        se.company = self.company
        se.posting_date = self.transfer_date
        se.cms_site_ref = self.name
        if self.to_project:
            se.project = self.to_project
        cost_center = get_cost_center(self.to_project, self.company)
        for item in self.items:
            se.append("items", {
                "item_code": item.item_code,
                "qty": flt(item.qty),
                "uom": item.uom,
                "s_warehouse": self.from_warehouse,
                "t_warehouse": self.to_warehouse,
                "batch_no": item.batch_no,
                "serial_no": item.serial_no,
                "cost_center": cost_center,
                "project": self.to_project,
            })
        se.insert(ignore_permissions=True)
        se.submit()
        self.db_set("stock_entry_ref", se.name)
        frappe.msgprint(_("Stock Entry {0} created and submitted").format(se.name))
