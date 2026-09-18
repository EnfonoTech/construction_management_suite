import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate

from construction_management_suite.utils.accounting import get_cost_center
from construction_management_suite.utils.billing import (
    add_line,
    billing_item,
)
from construction_management_suite.utils.validations import validate_project_company
from construction_management_suite.utils.billing import (
    calculate_taxes as calculate_document_taxes,
    carry_taxes,
    company_setting,
)
from construction_management_suite.utils.titles import (
    month_of,
    project_label,
    set_auto_title,
)


class SubcontractorPaymentCertificate(Document):
    def validate(self):
        if not self.taxes_and_charges and not self.taxes:
            self.taxes_and_charges = company_setting(self.company, "purchase_taxes_template")
        set_auto_title(self, "certificate_title", [self.subcontractor, project_label(self.project), month_of(self.submission_date)])
        validate_project_company(self)
        self.calculate_totals()
        self.calculate_document_taxes()

    def invoice_line_description(self):
        """The subcontractor and the certificate period, not an internal id."""
        from construction_management_suite.utils.titles import month_of, project_label

        parts = [_("Subcontract Payment"), self.subcontractor,
                 project_label(self.project), month_of(self.submission_date)]
        return " — ".join(str(p).strip() for p in parts if p and str(p).strip())

    def calculate_document_taxes(self):
        """Taxes on the agreed value, passed through to the document this raises."""
        tax = calculate_document_taxes(self, self.net_payable)
        self.total_payable = flt(self.net_payable) + tax

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
        add_line(pi, work, self.invoice_line_description(),
                 self.net_payable, cost_center=cost_center)

        carry_taxes(self, pi, cost_center)
        pi.insert(ignore_permissions=True)
        self.db_set("purchase_invoice_ref", pi.name)
        frappe.msgprint(_("Purchase Invoice {0} created").format(pi.name))
