import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate

from construction_management_suite.utils.accounting import get_cost_center
from construction_management_suite.utils.titles import project_label
from construction_management_suite.utils.validations import validate_project_company
from construction_management_suite.utils.billing import (
    add_line,
    money,
    billing_item,
    calculate_taxes as calculate_document_taxes,
    carry_taxes,
    company_setting,
    load_tax_template,
)


class RetentionRelease(Document):
    """Claiming back the money the client held from every certificate.

    Retention accumulates across all certificates on a project and is normally
    released in two tranches — half at Practical Completion, half at the end of
    the defects liability period. The held and released figures are recomputed
    from the certificates themselves rather than typed, so two releases raised
    on the same project cannot between them claim more than was ever held.
    """

    def validate(self):
        if not self.taxes_and_charges and not self.taxes:
            self.taxes_and_charges = company_setting(self.company, "sales_taxes_template")
        load_tax_template(self)
        validate_project_company(self)
        self.set_retention_position()
        self.validate_release_amount()
        self.calculate_document_taxes()

    def calculate_document_taxes(self):
        """Taxes on the agreed value, passed through to the document this raises."""
        tax = calculate_document_taxes(self, self.release_amount)
        self.total_payable = flt(self.release_amount) + tax

    def before_submit(self):
        self.status = "Approved"
        if not self.approval_date:
            self.approval_date = nowdate()

    def before_cancel(self):
        # before, not on_cancel: on_cancel runs after the row is written.
        self.status = "Cancelled"

    def on_submit(self):
        self._create_sales_invoice()

    def on_cancel(self):
        self._cancel_linked_invoice()

    # ----- Position -----

    def set_retention_position(self):
        self.total_retention_held = self._held()
        # Stored rather than recomputed each time it is needed: the form has to
        # reach the same balance as the server, and it cannot query other
        # releases from the browser.
        self.released_to_date = self._released()
        self.balance_retention = (
            flt(self.total_retention_held)
            - flt(self.released_to_date)
            - flt(self.release_amount)
        )

    def _held(self):
        """Everything withheld across the project's submitted certificates."""
        total = frappe.db.sql(
            """
            SELECT SUM(retention_amount) FROM `tabInterim Payment Certificate`
            WHERE project = %s AND docstatus = 1
            """,
            self.project,
        )[0][0]
        return flt(total)

    def _released(self):
        """Everything already released by other submitted releases."""
        total = frappe.db.sql(
            """
            SELECT SUM(release_amount) FROM `tabRetention Release`
            WHERE project = %s AND docstatus = 1 AND name != %s
            """,
            (self.project, self.name or ""),
        )[0][0]
        return flt(total)

    def validate_release_amount(self):
        if flt(self.release_amount) <= 0:
            frappe.throw(_("Release Amount must be greater than zero"))

        outstanding = flt(self.total_retention_held) - flt(self.released_to_date)
        if flt(self.release_amount) > outstanding + 0.005:
            frappe.throw(
                _("Cannot release {0}. Only {1} is still held on this project "
                  "({2} withheld, {3} already released).").format(
                    money(self, self.release_amount),
                    money(self, outstanding),
                    money(self, self.total_retention_held),
                    money(self, self.released_to_date),
                ),
                title=_("Over-release"),
            )

    # ----- ERPNext Integration -----

    def _create_sales_invoice(self):
        if not self.client:
            return
        si = frappe.new_doc("Sales Invoice")
        si.customer = self.client
        si.project = self.project
        si.company = self.company
        si.currency = self.currency
        si.cms_retention_release_ref = self.name
        cost_center = get_cost_center(self.project, self.company)
        add_line(
            si,
            billing_item("retention_release_item"),
            _("Release of retention held on {0} — {1}").format(
                project_label(self.project), self.release_type
            ),
            self.release_amount,
            cost_center=cost_center,
        )
        carry_taxes(self, si, cost_center)
        si.insert(ignore_permissions=True)
        self.db_set("sales_invoice_ref", si.name)
        self.db_set("status", "Invoiced")
        frappe.msgprint(_("Sales Invoice {0} created").format(si.name))

    def _cancel_linked_invoice(self):
        if not self.sales_invoice_ref:
            return
        si = frappe.get_doc("Sales Invoice", self.sales_invoice_ref)
        if si.docstatus == 1:
            si.cancel()
