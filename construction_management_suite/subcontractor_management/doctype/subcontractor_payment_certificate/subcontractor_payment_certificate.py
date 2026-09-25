import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, formatdate, getdate, nowdate

from construction_management_suite.utils.accounting import get_cost_center
from construction_management_suite.utils.billing import (
    add_line,
    billing_item,
    default_tax_template,
)
from construction_management_suite.utils.validations import (
    validate_one_row_per_work_item,
    validate_project_company,
)
from construction_management_suite.utils.billing import (
    calculate_taxes as calculate_document_taxes,
    carry_taxes,
    load_tax_template,
    money,
    refuse_empty,
    stamp_accounting,
    validate_tax_template_company,
)
from construction_management_suite.utils.titles import (
    month_of,
    project_label,
    set_auto_title,
)


class SubcontractorPaymentCertificate(Document):
    def validate(self):
        if not self.taxes_and_charges and not self.taxes:
            self.taxes_and_charges = default_tax_template(self)
        validate_tax_template_company(self)
        load_tax_template(self)
        set_auto_title(self, "certificate_title", [self.subcontractor, project_label(self.project), month_of(self.submission_date)])
        validate_project_company(self)
        validate_one_row_per_work_item(self.items)
        self.validate_period()
        self.set_work_numbers()
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
        """Tax on what is invoiced, which is the certified value — not the net.

        The invoice this raises is for the work certified, at its gross value.
        Retention and the recoveries are withheld from the payment, not taken
        off the supply, so they do not reduce the taxable amount: the tax point
        is certification, and the retained part is taxed now and paid later.

        `total_payable` stays what it always was — what to pay this month — so
        it is the net plus that tax, and the difference between it and the
        invoice is exactly what is being withheld.
        """
        tax = calculate_document_taxes(self, self.certified_amount)
        # Two different figures, and the document used to show only one of them.
        # The supplier invoices the certified value plus its tax; what you pay
        # this month is that less what is being withheld. The difference is the
        # retention, which stays outstanding against the invoice until released.
        self.invoice_amount = flt(self.certified_amount) + tax
        self.total_withheld = (
            flt(self.retention_deduction) + flt(self.advance_recovery) + flt(self.other_deductions)
        )
        self.total_payable = flt(self.net_payable) + tax

    @frappe.whitelist()
    def get_completed_from_work_orders(self):
        """Claim what the work orders record as built and not yet certified."""
        from construction_management_suite.api.boq import get_completed_work

        if not self.subcontract_agreement:
            frappe.throw(_("Choose the agreement this certificate is against"))
        existing = {i.item_code for i in self.items if i.item_code}
        added = 0
        for line in get_completed_work(self.subcontract_agreement, certificate=self.name):
            if line["item_code"] in existing:
                continue
            self.append("items", line)
            added += 1
        return added

    def validate_period(self):
        """A certificate covers a period, and a period runs forwards."""
        if not (self.period_from and self.period_to):
            return
        if getdate(self.period_from) > getdate(self.period_to):
            frappe.throw(
                _("The period runs from {0} to {1}, which is backwards.").format(
                    formatdate(self.period_from), formatdate(self.period_to)
                ),
                title=_("Check the period"),
            )

    def set_work_numbers(self):
        """Fill the bill number beside each work item. Display only."""
        from construction_management_suite.material_planning.doctype.material_consumption_entry.material_consumption_entry import (
            work_no_map,
        )

        numbers = work_no_map(self.project) if self.project else {}
        for row in self.items:
            row.work_no = numbers.get(row.item_code)

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

    def order_rows(self):
        """The agreement's Purchase Order lines, by item, with the order's name.

        Matching on the item is what lets the invoice bill the order rather
        than sit beside it. Where an order releases the same work item on two
        lines the first is used — a second line for the same item is the order
        being amended, and the amendment carries its own row.
        """
        order = frappe.db.get_value(
            "Subcontract Agreement", self.subcontract_agreement, "purchase_order_ref"
        )
        if not order or frappe.db.get_value("Purchase Order", order, "docstatus") != 1:
            return None, {}
        rows = {}
        for row in frappe.get_all(
            "Purchase Order Item",
            filters={"parent": order},
            fields=["name", "item_code"],
            order_by="idx",
        ):
            rows.setdefault(row.item_code, row.name)
        return order, rows

    def _create_purchase_invoice(self):
        pi = frappe.new_doc("Purchase Invoice")
        pi.supplier = self.subcontractor
        pi.company = self.company
        pi.currency = self.currency
        pi.project = self.project
        pi.cms_subcontract_certificate_ref = self.name
        cost_center = get_cost_center(self.project, self.company)

        # One line per certified item, at its gross value, pointed at the order
        # row it bills. A single net line could not be: ERPNext refuses a
        # `po_detail` whose item does not match, so nothing relieved the order
        # and it stayed open at 0% billed for ever.
        #
        # Retention and the recoveries are NOT deducted here. They are withheld
        # from the payment: the invoice stands at its gross and the withheld
        # part remains outstanding against it until it is released, which is
        # then simply another payment against the same invoice.
        order, po_rows = self.order_rows()
        claimed = flt(self.gross_amount_claimed)
        # An engineer who certifies less than was claimed is certifying every
        # line down in proportion; the measured quantities stay as measured.
        factor = flt(self.certified_amount) / claimed if claimed else 1

        for row in self.items:
            if flt(row.amount_claimed) <= 0:
                continue
            qty = flt(row.qty_completed) or 1
            line = {
                "item_code": row.item_code,
                "description": row.description or row.item_code,
                "qty": qty,
                "rate": flt(row.amount_claimed) * factor / qty,
                "project": self.project,
                "cost_center": cost_center,
            }
            if row.uom:
                line["uom"] = row.uom
                line["conversion_factor"] = 1
            if row.item_code in po_rows:
                line["purchase_order"] = order
                line["po_detail"] = po_rows[row.item_code]
            pi.append("items", line)

        if not pi.items:
            # Nothing measured — a certificate for a lump sum still has to be
            # payable, so fall back to the module's own billing item.
            add_line(pi, billing_item("subcontract_billing_item"),
                     self.invoice_line_description(), self.certified_amount,
                     cost_center=cost_center)

        refuse_empty(pi, self)
        carry_taxes(self, pi, cost_center)
        stamp_accounting(pi, project=self.project, cost_center=cost_center)
        pi.insert(ignore_permissions=True)
        self.db_set("purchase_invoice_ref", pi.name)
        frappe.msgprint(_("Purchase Invoice {0} created").format(pi.name))
