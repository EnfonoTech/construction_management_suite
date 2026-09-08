import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate

from construction_management_suite.utils.accounting import get_cost_center
from construction_management_suite.utils.validations import validate_project_company


class SubcontractorPaymentCertificate(Document):
    def validate(self):
        validate_project_company(self)
        self.calculate_totals()

    def calculate_totals(self):
        self.gross_amount_claimed = sum(flt(i.amount_claimed) for i in self.items)
        self.retention_deduction = flt(self.certified_amount) * flt(self.retention_percent) / 100
        self.net_payable = (
            flt(self.certified_amount)
            - flt(self.retention_deduction)
            - flt(self.advance_recovery)
            - flt(self.other_deductions)
        )

    def before_submit(self):
        self.submitted_by = frappe.session.user
        self.submission_date = nowdate()
        self.status = "Submitted"

    def on_submit(self):
        self._create_purchase_invoice()

    def _create_purchase_invoice(self):
        pi = frappe.new_doc("Purchase Invoice")
        pi.supplier = self.subcontractor
        pi.company = self.company
        pi.currency = self.currency
        pi.project = self.project
        pi.append("items", {
            "item_name": f"Subcontract Payment — {self.subcontract_agreement}",
            "description": self.certificate_title,
            "qty": 1,
            "rate": self.net_payable,
            "uom": "Nos",
            # No item_code, so ERPNext cannot derive these — set them explicitly.
            "expense_account": frappe.get_cached_value(
                "Company", self.company, "default_expense_account"
            ),
            "cost_center": get_cost_center(self.project, self.company),
        })
        pi.insert(ignore_permissions=True)
        self.db_set("purchase_invoice_ref", pi.name)
        frappe.msgprint(_("Purchase Invoice {0} created").format(pi.name))
