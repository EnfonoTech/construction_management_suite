import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate

from construction_management_suite.utils.accounting import get_cost_center
from construction_management_suite.utils.billing import (
    add_deduction,
    add_line,
    apply_taxes,
    billing_item,
    warn_unapplied,
)
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
        cost_center = get_cost_center(self.project, self.company)
        work = billing_item("subcontract_billing_item")
        add_line(pi, work, self.certificate_title or self.subcontract_agreement,
                 self.certified_amount, cost_center=cost_center)

        # Same reasoning as the client certificate — see utils.billing.
        unapplied = []
        for amount, setting, label in (
            (self.retention_deduction, "retention_account",
             _("Retention @ {0}%").format(flt(self.retention_percent))),
            (self.advance_recovery, "advance_recovery_account", _("Advance Recovery")),
            (self.other_deductions, "other_deductions_account", _("Other Deductions")),
        ):
            left = add_deduction(pi, setting, label, amount, self.company, cost_center)
            unapplied.append((label, left))

        if not pi.items:
            add_line(pi, work, self.certificate_title, self.net_payable, cost_center=cost_center)

        apply_taxes(pi, "purchase_taxes_template")
        pi.insert(ignore_permissions=True)
        self.db_set("purchase_invoice_ref", pi.name)
        warn_unapplied(pi, unapplied)
        frappe.msgprint(_("Purchase Invoice {0} created").format(pi.name))
