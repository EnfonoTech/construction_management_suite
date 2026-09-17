import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate

from construction_management_suite.utils.accounting import get_cost_center
from construction_management_suite.utils.billing import (
    add_deduction,
    add_line,
    billing_item,
    warn_unapplied,
)
from construction_management_suite.utils.settings import action_for, cms_setting, enforce
from construction_management_suite.utils.validations import validate_project_company


class InterimPaymentCertificate(Document):
    def onload(self):
        self.set_onload("progress", self.get_progress())

    def validate(self):
        validate_project_company(self)
        self.set_contract_value()
        self.set_previous_claimed()
        self.calculate_items()
        self.calculate_deductions()
        self.calculate_taxes()
        self.validate_over_certification()

    def set_contract_value(self):
        """Fall back to the BOQ's total when nobody has stated a contract value.

        It was only ever filled by the form script, so a certificate raised any
        other way had nothing to measure progress against. Only filled when
        blank — an approved Variation Order moves the contract sum away from the
        original bill, and that figure must win.
        """
        if not flt(self.retention_percent):
            self.retention_percent = flt(cms_setting("default_retention_percent", 0))
        if not self.taxes_and_charges and not self.taxes:
            self.taxes_and_charges = cms_setting("sales_taxes_template")
        if flt(self.contract_value) or not self.boq_ref:
            return
        self.contract_value = flt(frappe.db.get_value("BOQ", self.boq_ref, "grand_total"))

    def set_previous_claimed(self):
        """Refresh each line's opening position from the certificates already signed.

        Typed by hand this is how a client gets billed twice for the same work,
        and it goes stale the moment an earlier certificate is cancelled, so it
        is recomputed on every save rather than trusted.
        """
        from construction_management_suite.api.boq import _previously_claimed_by_line

        if not self.project:
            return
        claimed = _previously_claimed_by_line(self.project, exclude_ipc=self.name)
        for item in self.items:
            if item.boq_item_ref:
                item.previous_qty_claimed = flt(claimed.get(item.boq_item_ref))

    def calculate_items(self):
        gross = 0
        for item in self.items:
            item.contract_amount = flt(item.contract_qty) * flt(item.contract_rate)
            item.cumulative_qty = flt(item.previous_qty_claimed) + flt(item.qty_this_period)
            item.amount_this_period = flt(item.qty_this_period) * flt(item.contract_rate)
            item.cumulative_amount = flt(item.cumulative_qty) * flt(item.contract_rate)
            item.remaining_qty = flt(item.contract_qty) - flt(item.cumulative_qty)
            if flt(item.contract_amount) > 0:
                item.percent_complete = flt(item.cumulative_amount) / flt(item.contract_amount) * 100
            gross += flt(item.amount_this_period)
        self.gross_amount_this_period = gross
        self.cumulative_amount_to_date = flt(self.previous_cumulative_amount) + gross

    def validate_over_certification(self):
        """Never certify more of a line than the contract contains.

        Extra work is a Variation Order, which reprices it and extends the
        contract sum. Letting it through here instead would bill the client for
        work no contract covers and leave the BOQ showing more built than sold.
        """
        action = action_for("over_certification_action", "Stop")
        for item in self.items:
            if flt(item.qty_this_period) < 0:
                frappe.throw(
                    _("Row {0}: Qty This Period cannot be negative. To reverse an "
                      "over-certification, cancel the certificate that made it.").format(item.idx)
                )
            if not flt(item.contract_qty):
                continue
            # Quantities are re-measured on site; a hair over the contract figure
            # is rounding, not a claim.
            if flt(item.cumulative_qty) - flt(item.contract_qty) > 0.0001:
                if action == "Ignore":
                    continue
                enforce(
                    action,
                    _(
                        "Row {0} ({1}): certifying {2} on top of {3} already certified "
                        "comes to {4}, but the contract quantity is only {5}.<br><br>"
                        "Raise a <b>Variation Order</b> for the extra work, or reduce "
                        "this period's quantity to {6}."
                    ).format(
                        item.idx,
                        item.item_code or item.description,
                        flt(item.qty_this_period),
                        flt(item.previous_qty_claimed),
                        flt(item.cumulative_qty),
                        flt(item.contract_qty),
                        flt(item.contract_qty) - flt(item.previous_qty_claimed),
                    ),
                    title=_("Over-certification"),
                )

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

    def calculate_taxes(self):
        """Apply the tax table to the certified value, ERPNext's own way.

        Charged on the GROSS, not the net: the supply is the work done, and
        retention is withheld afterwards rather than discounting it. The four
        charge types follow `calculate_taxes` in erpnext's taxes_and_totals, so
        the certificate and the invoice it raises reach the same figure.
        """
        running = flt(self.gross_amount_this_period)
        for row in self.taxes:
            if row.charge_type == "Actual":
                amount = flt(row.tax_amount)
            elif row.charge_type == "On Net Total":
                amount = flt(self.gross_amount_this_period) * flt(row.rate) / 100
            elif row.charge_type == "On Previous Row Amount":
                amount = flt(self._previous_row(row, "tax_amount")) * flt(row.rate) / 100
            elif row.charge_type == "On Previous Row Total":
                amount = flt(self._previous_row(row, "total")) * flt(row.rate) / 100
            else:
                # On Item Quantity needs per-item tax handling this document does
                # not have; refusing is better than a figure nobody can explain.
                frappe.throw(
                    _("Row {0}: charge type {1} is not supported on a certificate").format(
                        row.idx, row.charge_type
                    )
                )
            row.tax_amount = amount
            running += amount
            row.total = running

        self.total_taxes_and_charges = sum(flt(r.tax_amount) for r in self.taxes)
        self.total_payable = flt(self.net_payable_this_period) + flt(self.total_taxes_and_charges)

    def _previous_row(self, row, fieldname):
        if not row.row_id:
            frappe.throw(_("Row {0}: set the row it is charged on").format(row.idx))
        idx = int(row.row_id)
        if idx >= row.idx:
            frappe.throw(_("Row {0} can only refer to a row above it").format(row.idx))
        return self.taxes[idx - 1].get(fieldname)

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
        self.push_certified_qty_to_boq()

    def before_cancel(self):
        # Not on_cancel: that runs after the row is written, so the status set
        # there is thrown away.
        self.status = "Cancelled"

    def on_cancel(self):
        self._cancel_linked_invoice()
        self.push_certified_qty_to_boq()

    # ----- Feeding the bill -----

    def push_certified_qty_to_boq(self):
        """Write each BOQ line's certified-to-date quantity back onto the bill.

        This is what makes BOQ variance mean anything: `actual_qty` is the work
        signed off, so `variance_qty` reads as built-versus-billed instead of
        the flat negative it showed while nothing ever wrote to it.

        Recomputed from every submitted certificate rather than incremented, so
        a cancellation corrects the bill instead of stranding it.
        """
        from construction_management_suite.api.boq import _previously_claimed_by_line

        if not self.boq_ref or not self.project:
            return
        certified = _previously_claimed_by_line(self.project)
        rows = frappe.get_all(
            "BOQ Item", filters={"parent": self.boq_ref}, fields=["name", "qty", "rate", "actual_qty"]
        )
        for row in rows:
            actual = flt(certified.get(row.name))
            if flt(row.actual_qty) == actual:
                continue
            variance_qty = actual - flt(row.qty)
            # db.set_value, not a doc save: the BOQ is submitted, and these three
            # are a report of what happened on site, not a change to the contract.
            frappe.db.set_value(
                "BOQ Item",
                row.name,
                {
                    "actual_qty": actual,
                    "variance_qty": variance_qty,
                    "variance_amount": variance_qty * flt(row.rate),
                },
                update_modified=False,
            )

    def get_progress(self):
        """Headline position of this certificate against the contract."""
        contract = flt(self.contract_value)
        cumulative = flt(self.cumulative_amount_to_date)
        return {
            "contract_value": contract,
            "cumulative_amount": cumulative,
            "percent_complete": (cumulative / contract * 100) if contract else 0,
            "balance_to_complete": contract - cumulative,
            "retention_held": flt(self.total_retention_held),
        }

    @frappe.whitelist()
    def get_items_from_boq(self):
        """Append every BOQ line with work left to certify."""
        from construction_management_suite.api.boq import get_boq_lines_for_ipc

        if not self.boq_ref:
            frappe.throw(_("Set the BOQ this certificate bills against first"))

        existing = {i.boq_item_ref for i in self.items if i.boq_item_ref}
        added = 0
        for line in get_boq_lines_for_ipc(self.boq_ref, ipc=self.name):
            if line["boq_item_ref"] in existing:
                continue
            self.append("items", line)
            added += 1
        return added

    def _create_sales_invoice(self):
        """Invoice the work line by line, with the deductions on the face of it.

        One lump sum carrying no item_code told the client nothing, could not be
        taxed, and left every sales report blind to what the money was for. The
        invoice now mirrors the certificate: a line per certified item at its
        contract rate, then the retention and recoveries as negative lines, so
        it still totals the net payable and every figure is visible.
        """
        if not self.client:
            return
        cost_center = get_cost_center(self.project, self.company)
        fallback = billing_item("progress_billing_item")

        si = frappe.new_doc("Sales Invoice")
        si.customer = self.client
        si.project = self.project
        si.company = self.company
        si.currency = self.currency
        si.cms_ipc_ref = self.name

        for item in self.items:
            if not flt(item.amount_this_period):
                continue
            add_line(
                si,
                item.item_code or fallback,
                item.description or item.item_code,
                item.amount_this_period,
                cost_center=cost_center,
                qty=flt(item.qty_this_period) or 1,
                rate=flt(item.contract_rate) if flt(item.qty_this_period) else flt(item.amount_this_period),
                uom=item.uom,
            )

        if not si.items:
            add_line(si, fallback, _("Progress Billing — {0}").format(self.ipc_title),
                     self.gross_amount_this_period, cost_center=cost_center)

        # Charge rows, not negative item lines: ERPNext will not submit a Sales
        # Invoice with a negative rate unless the whole site allows it, and a
        # negative line would understate revenue. A charge credits Sales with
        # the full certified value and parks the withheld amount in its own
        # account, which is what retention is — earned, owed, not yet due.
        unapplied, applied = [], 0
        for amount, setting, label in (
            (self.retention_amount, "retention_account",
             _("Retention @ {0}%").format(flt(self.retention_percent))),
            (self.advance_recovery_amount, "advance_recovery_account", _("Advance Recovery")),
            (self.other_deductions, "other_deductions_account", _("Other Deductions")),
        ):
            left = add_deduction(si, setting, label, amount, self.company, cost_center)
            unapplied.append((label, left))
            applied += flt(amount) - flt(left)

        # The certificate is the source: whatever was certified and taxed is
        # what gets invoiced, so the two documents cannot say different things.
        si.taxes_and_charges = self.taxes_and_charges
        for row in self.taxes:
            si.append("taxes", {
                "charge_type": row.charge_type,
                "account_head": row.account_head,
                "description": row.description,
                "rate": row.rate,
                "tax_amount": row.tax_amount,
                "row_id": row.row_id,
                "cost_center": row.cost_center or cost_center,
                "included_in_print_rate": row.included_in_print_rate,
            })
        si.insert(ignore_permissions=True)
        self.db_set("sales_invoice_ref", si.name)

        warn_unapplied(si, unapplied)
        frappe.msgprint(_("Sales Invoice {0} created").format(si.name))

    def _cancel_linked_invoice(self):
        if self.sales_invoice_ref:
            si = frappe.get_doc("Sales Invoice", self.sales_invoice_ref)
            if si.docstatus == 1:
                si.cancel()
