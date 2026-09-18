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
    money,
    carry_taxes,
    refuse_empty,
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
        self.validate_payable()
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

    @frappe.whitelist()
    def get_completed_from_work_orders(self):
        """Claim what the work orders record as built and not yet certified."""
        from construction_management_suite.api.boq import get_completed_work

        if not self.subcontract_agreement:
            frappe.throw(_("Choose the agreement this certificate is against"))
        existing = {i.work_order_item_ref for i in self.items if i.work_order_item_ref}
        added = 0
        for line in get_completed_work(self.subcontract_agreement, certificate=self.name):
            if line["work_order_item_ref"] in existing:
                continue
            self.append("items", line)
            added += 1
        return added

    def calculate_totals(self):
        self.gross_amount_claimed = sum(flt(i.amount_claimed) for i in self.items)
        # A certificate certifies what was claimed unless the engineer reduces
        # it. Left at zero it produced a nil payment and an invoice with no
        # lines at all, which ERPNext then crashed on.
        if not flt(self.certified_amount):
            self.certified_amount = flt(self.gross_amount_claimed)
        self.retention_deduction = flt(self.certified_amount) * flt(self.retention_percent) / 100
        self.net_payable = (
            flt(self.certified_amount)
            - flt(self.retention_deduction)
            - flt(self.advance_recovery)
            - flt(self.other_deductions)
        )

    def validate_payable(self):
        """There has to be something to pay before an invoice is raised."""
        if flt(self.net_payable) > 0:
            return
        frappe.throw(
            _(
                "Nothing is payable on this certificate: {0} certified less {1} "
                "in deductions leaves {2}. Certify an amount, or reduce the "
                "retention and recoveries."
            ).format(
                money(self, self.certified_amount),
                money(self, flt(self.retention_deduction) + flt(self.advance_recovery)
                      + flt(self.other_deductions)),
                money(self, self.net_payable),
            ),
            title=_("Nothing payable"),
        )

    def check_advance(self):
        """The advance you paid this trade, recovered from their certificates."""
        from construction_management_suite.utils.billing import check_advance_recovery

        if not self.subcontract_agreement:
            return
        sca = frappe.db.get_value("Subcontract Agreement", self.subcontract_agreement,
                                  ["advance_amount", "subcontract_value"], as_dict=True)
        if not sca or not flt(sca.advance_amount):
            return
        recovered = flt(frappe.db.sql(
            """SELECT SUM(advance_recovery) FROM `tabSubcontractor Payment Certificate`
               WHERE subcontract_agreement = %(a)s AND docstatus = 1 AND name != %(n)s""",
            {"a": self.subcontract_agreement, "n": self.name or ""})[0][0]) + flt(self.advance_recovery)
        billed = flt(self.previous_amount_certified) + flt(self.certified_amount)
        check_advance_recovery(self, sca.advance_amount, recovered, billed,
                               sca.subcontract_value, self.subcontractor)

    def before_submit(self):
        self.check_advance()
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

        refuse_empty(pi, self)
        carry_taxes(self, pi, cost_center)
        pi.insert(ignore_permissions=True)
        self.db_set("purchase_invoice_ref", pi.name)
        frappe.msgprint(_("Purchase Invoice {0} created").format(pi.name))
