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
    load_tax_template,
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
        load_tax_template(self)
        set_auto_title(self, "certificate_title", [self.subcontractor, project_label(self.project), month_of(self.submission_date)])
        validate_project_company(self)
        self.set_previous_certified()
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
        self.refresh_agreement()

    def before_cancel(self):
        # on_cancel runs after the row is written, so a status set there is lost.
        self.status = "Cancelled"

    def on_cancel(self):
        self._cancel_linked_invoice()
        self.refresh_agreement()

    def _cancel_linked_invoice(self):
        if not self.purchase_invoice_ref:
            return
        pi = frappe.get_doc("Purchase Invoice", self.purchase_invoice_ref)
        if pi.docstatus == 1:
            pi.cancel()
            frappe.msgprint(_("Purchase Invoice {0} cancelled").format(pi.name))

    def refresh_agreement(self):
        """Push the running totals back onto the agreement this bills against."""
        if not self.subcontract_agreement:
            return
        frappe.get_doc("Subcontract Agreement", self.subcontract_agreement).refresh_payment_summary()

    def set_previous_certified(self):
        """What earlier certificates on this agreement already certified.

        The form script filled it from the agreement, which was itself stale —
        two wrong numbers agreeing with each other. Read from the certificates
        themselves, on every save of a draft.
        """
        if self.docstatus != 0 or not self.subcontract_agreement:
            return
        total = frappe.db.sql(
            """SELECT SUM(certified_amount) FROM `tabSubcontractor Payment Certificate`
               WHERE subcontract_agreement = %(a)s AND docstatus = 1 AND name != %(n)s""",
            {"a": self.subcontract_agreement, "n": self.name or ""},
        )
        self.previous_amount_certified = flt(total[0][0]) if total else 0

    def _create_purchase_invoice(self):
        pi = frappe.new_doc("Purchase Invoice")
        pi.supplier = self.subcontractor
        pi.company = self.company
        pi.currency = self.currency
        pi.project = self.project
        pi.cms_subcontract_certificate_ref = self.name
        cost_center = get_cost_center(self.project, self.company)
        work = billing_item("subcontract_billing_item")
        add_line(pi, work, self.invoice_line_description(),
                 self.net_payable, cost_center=cost_center)

        carry_taxes(self, pi, cost_center)
        pi.insert(ignore_permissions=True)
        self.db_set("purchase_invoice_ref", pi.name)
        frappe.msgprint(_("Purchase Invoice {0} created").format(pi.name))
