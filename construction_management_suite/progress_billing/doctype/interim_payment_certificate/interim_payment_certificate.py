import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate

from construction_management_suite.utils.accounting import get_cost_center


class InterimPaymentCertificate(Document):
    def validate(self):
        self.calculate_items()
        self.calculate_deductions()

    def calculate_items(self):
        gross = 0
        for item in self.items:
            item.contract_amount = flt(item.contract_qty) * flt(item.contract_rate)
            item.cumulative_qty = flt(item.previous_qty_claimed) + flt(item.qty_this_period)
            item.amount_this_period = flt(item.qty_this_period) * flt(item.contract_rate)
            item.cumulative_amount = flt(item.cumulative_qty) * flt(item.contract_rate)
            if flt(item.contract_amount) > 0:
                item.percent_complete = flt(item.cumulative_amount) / flt(item.contract_amount) * 100
            gross += flt(item.amount_this_period)
        self.gross_amount_this_period = gross
        self.cumulative_amount_to_date = flt(self.previous_cumulative_amount) + gross

    def calculate_deductions(self):
        self.retention_amount = flt(self.gross_amount_this_period) * flt(self.retention_percent) / 100
        total_deductions = (
            flt(self.retention_amount)
            + flt(self.advance_recovery_amount)
            + flt(self.other_deductions)
        )
        self.net_payable_this_period = flt(self.gross_amount_this_period) - total_deductions
        # Accumulate total retention held
        prev_retention = self._get_previous_retention()
        self.total_retention_held = prev_retention + flt(self.retention_amount)

    def _get_previous_retention(self):
        prev = frappe.db.sql(
            """
            SELECT SUM(retention_amount) AS total
            FROM `tabInterim Payment Certificate`
            WHERE project = %s AND docstatus = 1 AND name != %s
            """,
            (self.project, self.name or ""),
            as_dict=True,
        )
        return flt((prev[0] or {}).get("total", 0))

    def before_submit(self):
        self.submitted_by = frappe.session.user
        self.submission_date = nowdate()
        self.status = "Submitted"

    def on_submit(self):
        self._create_sales_invoice()

    def on_cancel(self):
        self._cancel_linked_invoice()
        self.status = "Draft"

    def _create_sales_invoice(self):
        if not self.client:
            return
        si = frappe.new_doc("Sales Invoice")
        si.customer = self.client
        si.project = self.project
        si.company = self.company
        si.currency = self.currency
        si.cms_ipc_ref = self.name
        si.append("items", {
            "item_name": f"IPC #{self.ipc_number} — {self.project}",
            "description": f"Progress Billing — {self.ipc_title}",
            "qty": 1,
            "rate": self.net_payable_this_period,
            "uom": "Nos",
            # No item_code, so ERPNext cannot derive these — set them explicitly.
            "income_account": frappe.get_cached_value(
                "Company", self.company, "default_income_account"
            ),
            "cost_center": get_cost_center(self.project, self.company),
        })
        si.insert(ignore_permissions=True)
        self.db_set("sales_invoice_ref", si.name)
        frappe.msgprint(_("Sales Invoice {0} created").format(si.name))

    def _cancel_linked_invoice(self):
        if self.sales_invoice_ref:
            si = frappe.get_doc("Sales Invoice", self.sales_invoice_ref)
            if si.docstatus == 1:
                si.cancel()
