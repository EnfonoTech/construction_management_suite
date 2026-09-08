import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from construction_management_suite.utils.accounting import get_cost_center


class SubcontractAgreement(Document):
    def validate(self):
        self.calculate_advance()
        self.fetch_payment_summary()

    def calculate_advance(self):
        self.advance_amount = flt(self.subcontract_value) * flt(self.advance_percent) / 100

    def fetch_payment_summary(self):
        if self.is_new():
            return
        data = frappe.db.sql(
            """
            SELECT
                SUM(gross_amount_claimed) AS total_claimed,
                SUM(certified_amount) AS total_certified,
                SUM(net_payable) AS total_paid
            FROM `tabSubcontractor Payment Certificate`
            WHERE subcontract_agreement = %s AND docstatus = 1
            """,
            self.name,
            as_dict=True,
        )[0]
        self.total_claimed = flt(data.total_claimed)
        self.total_certified = flt(data.total_certified)
        self.total_paid = flt(data.total_paid)
        self.balance_due = flt(self.subcontract_value) - flt(self.total_paid)

    def on_submit(self):
        self.status = "Active"
        self._create_purchase_order()

    def _create_purchase_order(self):
        """Create a linked ERPNext PO on first submission."""
        if self.purchase_order_ref:
            return
        po = frappe.new_doc("Purchase Order")
        po.supplier = self.subcontractor
        po.company = self.company
        po.currency = self.currency
        po.project = self.project
        po.cms_subcontract_ref = self.name
        po.schedule_date = self.end_date or frappe.utils.add_months(frappe.utils.nowdate(), 6)
        if self.payment_terms:
            po.payment_terms_template = self.payment_terms
        cost_center = get_cost_center(self.project, self.company)
        for item in self.items:
            po.append("items", {
                "item_code": item.item_code,
                "item_name": item.description,
                "description": item.description,
                "qty": item.qty,
                "uom": item.uom,
                "rate": item.rate,
                "project": self.project,
                "cost_center": cost_center,
                "schedule_date": po.schedule_date,
            })
        po.insert(ignore_permissions=True)
        self.db_set("purchase_order_ref", po.name)
        frappe.msgprint(_("Purchase Order {0} created").format(po.name))
