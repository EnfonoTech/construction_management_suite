import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate

from construction_management_suite.utils.accounting import get_cost_center
from construction_management_suite.utils.billing import (
    calculate_taxes as calculate_document_taxes,
    carry_taxes,
    company_setting,
    load_tax_template,
    add_line,
    billing_item,
)
from construction_management_suite.utils.settings import action_for, cms_setting, enforce
from construction_management_suite.utils.validations import validate_project_company
from construction_management_suite.utils.titles import (
    month_of,
    project_label,
    set_auto_title,
)


class InterimPaymentCertificate(Document):
    def onload(self):
        self.set_onload("progress", self.get_progress())

    def validate(self):
        set_auto_title(self, "ipc_title", [_("IPC #{0}").format(self.ipc_number) if self.ipc_number else _("Certificate"), project_label(self.project), month_of(self.billing_period_to)])
        validate_project_company(self)
        self.set_contract_value()
        self.set_previous_position()
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
            self.taxes_and_charges = company_setting(self.company, "sales_taxes_template")
        load_tax_template(self)
        if flt(self.contract_value) or not self.boq_ref:
            return
        self.contract_value = flt(frappe.db.get_value("BOQ", self.boq_ref, "grand_total"))

    def set_previous_position(self):
        """Where the last certificate on this project left off.

        Only the form script filled this before, so a certificate raised by the
        API, an import or get_mapped_doc opened at zero and reported a
        cumulative-to-date that was really just this period. Recomputed on every
        save of a draft, because an earlier certificate being submitted or
        cancelled moves it.
        """
        if self.docstatus != 0 or not self.project:
            return
        from construction_management_suite.api.boq import get_previous_ipc_position

        position = get_previous_ipc_position(self.project, exclude_ipc=self.name)
        self.previous_cumulative_amount = flt(position.get("cumulative_amount"))
        if not self.ipc_number:
            self.ipc_number = position.get("next_ipc_number")

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
        """Charged on the net payable — the same base the invoice uses.

        The invoice is raised for what is due after retention and the
        recoveries, so taxing anything else here would put the certificate and
        the invoice at different figures.
        """
        tax = calculate_document_taxes(self, self.net_payable_this_period)
        self.total_payable = flt(self.net_payable_this_period) + tax

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
        """One line for the net payable, using the Item named in the settings.

        Retention and the recoveries have already come off by this point — the
        invoice is for what is actually due, and the certificate carries the
        breakdown showing how it got there.
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

        # One line for the certificate, not a line per certified item. The
        # invoice answers "what is this payment for"; the certificate itself is
        # the breakdown, and repeating it here just makes the two drift.
        add_line(
            si,
            fallback,
            self.invoice_line_description(),
            self.net_payable_this_period,
            cost_center=cost_center,
        )

        carry_taxes(self, si, cost_center)

        si.insert(ignore_permissions=True)
        self.db_set("sales_invoice_ref", si.name)

        frappe.msgprint(_("Sales Invoice {0} created").format(si.name))

    def invoice_line_description(self):
        """What the client reads on the invoice line.

        The project's NAME, not its id — PROJ-0007 means nothing to the person
        approving the payment — plus the certificate number, which is what both
        sides quote in correspondence.
        """
        from construction_management_suite.utils.titles import month_of, project_label

        parts = [
            _("Interim Payment Certificate No. {0}").format(self.ipc_number or ""),
            project_label(self.project),
            month_of(self.billing_period_to),
        ]
        return " — ".join(str(p).strip() for p in parts if p and str(p).strip())

    def _cancel_linked_invoice(self):
        if self.sales_invoice_ref:
            si = frappe.get_doc("Sales Invoice", self.sales_invoice_ref)
            if si.docstatus == 1:
                si.cancel()
