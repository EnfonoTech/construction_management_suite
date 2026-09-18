import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from construction_management_suite.utils.accounting import get_cost_center
from construction_management_suite.utils.settings import cms_setting
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


class SubcontractAgreement(Document):
    def set_missing_defaults(self):
        """Retention from the module default; the docfield's hardcoded 10 beat it."""
        if not flt(self.retention_percent):
            self.retention_percent = flt(
                cms_setting("default_subcontract_retention_percent", 0)
            )

    def validate(self):
        if not self.taxes_and_charges and not self.taxes:
            self.taxes_and_charges = company_setting(self.company, "purchase_taxes_template")
        load_tax_template(self)
        set_auto_title(self, "agreement_title", [self.subcontractor, project_label(self.project), self.scope_summary if self.get("scope_summary") else None])
        self.set_missing_defaults()
        validate_project_company(self)
        self.calculate_items()
        self.calculate_advance()
        self.fetch_payment_summary()
        self.calculate_document_taxes()

    def calculate_document_taxes(self):
        """Taxes on the agreed value, passed through to the document this raises."""
        tax = calculate_document_taxes(self, self.subcontract_value)
        self.total_with_taxes = flt(self.subcontract_value) + tax

    def calculate_items(self):
        for row in self.items:
            row.amount = flt(row.qty) * flt(row.rate)
        # The value was typed by hand while a priced schedule sat right above
        # it, so the two could disagree and nothing said so. A lump-sum
        # agreement with no schedule can still state its own value.
        if self.items:
            self.subcontract_value = sum(flt(r.amount) for r in self.items)

    def calculate_advance(self):
        self.advance_amount = flt(self.subcontract_value) * flt(self.advance_percent) / 100

    @frappe.whitelist()
    def refresh_payment_summary(self):
        """Recompute and store, for an agreement already submitted.

        validate() only runs while a document is being saved, and nobody saves
        a signed agreement — so the running totals froze at zero the moment it
        was submitted, and the aging job and the balance due read them.
        Certificates call this on submit and on cancel.
        """
        self.fetch_payment_summary()
        for field in ("total_claimed", "total_certified", "total_paid", "balance_due"):
            self.db_set(field, flt(self.get(field)), update_modified=False)

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

    def before_submit(self):
        self.status = "Active"

    def before_cancel(self):
        # before, not on_cancel: on_cancel runs after the row is written.
        self.status = "Cancelled"

    def on_submit(self):
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
