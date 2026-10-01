import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from construction_management_suite.utils.accounting import get_cost_center, get_warehouse
from construction_management_suite.utils.settings import action_for, cms_setting, enforce
from construction_management_suite.utils.validations import (
    validate_one_row_per_work_item,
    validate_project_company,
)
from construction_management_suite.utils.billing import (
    calculate_taxes as calculate_document_taxes,
    carry_taxes,
    default_tax_template,
    load_tax_template,
    stamp_accounting,
    validate_tax_template_company,
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
            self.taxes_and_charges = default_tax_template(self)
        load_tax_template(self)
        validate_tax_template_company(self)
        set_auto_title(self, "agreement_title", [self.subcontractor, project_label(self.project), self.scope_summary if self.get("scope_summary") else None])
        self.set_missing_defaults()
        validate_project_company(self)
        validate_one_row_per_work_item(self.items)
        self.calculate_items()
        self.fill_costed_rates()
        self.check_stock_lines_have_a_store()
        self.check_against_estimate()
        self.check_against_the_library()
        self.check_against_budget()
        self.calculate_advance()
        self.fetch_payment_summary()
        self.calculate_document_taxes()

    def calculate_document_taxes(self):
        """Taxes on the agreed value, passed through to the document this raises."""
        tax = calculate_document_taxes(self, self.subcontract_value)
        self.total_with_taxes = flt(self.subcontract_value) + tax

    @frappe.whitelist()
    def add_work_lines(self, rows):
        """Append the chosen lines of work for this trade to price."""
        import json as _json

        if isinstance(rows, str):
            rows = _json.loads(rows)
        existing = {i.item_code for i in self.items if i.item_code}
        added = 0
        for row in rows:
            if row.get("item_code") in existing:
                continue
            self.append("items", row)
            added += 1
        return added

    def fill_costed_rates(self):
        """What the estimate costed each line at, for rows picked by hand.

        `Get Work from the Estimate` fills this; choosing an item from the
        picker did not, so the "above the costed rate" check skipped every row
        somebody added themselves — which is most of them once a subcontract
        priced *inside* a line of work can be chosen at all.

        A line of work is costed at its unit cost. A subcontract resource is
        costed at its own rate on the analysis it sits in. Only ever filled
        where it is blank: a rate somebody typed is theirs.
        """
        from construction_management_suite.utils.validations import current_estimate

        blank = [row for row in self.items if row.item_code and not flt(row.boq_cost_rate)]
        if not blank or not self.project:
            return
        estimate = current_estimate(self.project)
        if not estimate:
            return

        costed = {
            row.item_code: flt(row.unit_cost)
            for row in frappe.get_all(
                "Cost Estimation Item",
                filters={"parent": estimate},
                fields=["item_code", "unit_cost"],
            )
        }
        for row in frappe.db.sql(
            """
            SELECT r.resource_item AS code, r.rate AS rate
            FROM `tabRate Analysis Resource` r
            JOIN `tabCost Estimation Item` i ON i.rate_analysis_ref = r.parent
            WHERE i.parent = %s AND r.resource_type = 'Subcontract'
              AND IFNULL(r.resource_item, '') != ''
            """,
            estimate,
            as_dict=True,
        ):
            costed.setdefault(row.code, flt(row.rate))

        for row in blank:
            if costed.get(row.item_code):
                row.boq_cost_rate = costed[row.item_code]

    def check_stock_lines_have_a_store(self):
        """A material let to a trade has to be delivered somewhere.

        Subcontracting used to mean a whole line of work, which is a service —
        no warehouse, no question. Now that a resource priced inside a line can
        be let, a material can reach the order, and ERPNext refuses a stock line
        with no warehouse: `Row #1: Warehouse is mandatory for stock Item`.

        `Deliver To` on the agreement answers it, defaulting to the project's
        own store and overridable per agreement — a trade may take delivery
        somewhere other than the site the job usually works from. Asked here,
        where somebody is looking at a form, rather than out of the purchase
        order a submission happens to create.
        """
        if not self.warehouse:
            self.warehouse = get_warehouse(self.project, self.company)

        stock = [
            item for item in self.items
            if item.item_code
            and frappe.db.get_value("Item", item.item_code, "is_stock_item")
        ]
        if not stock or self.warehouse:
            return
        frappe.throw(
            "<br>".join(
                _("Row {0}: {1}").format(item.idx, item.item_code) for item in stock[:10]
            )
            + _(
                "<br><br>These are stock items, so the order has to say where they "
                "are delivered. Fill in <b>Deliver To</b>, give {0} a Default "
                "Warehouse, or let the work itself rather than the material inside "
                "it."
            ).format(self.project or _("the project")),
            title=_("{0} line(s) need a store").format(len(stock)),
        )

    def check_against_estimate(self):
        """Flag paying a trade more than the work was costed at.

        Not an error — a trade rate can beat your own gang, or a specialist may
        simply cost more than the estimate assumed — but engaging someone above
        the costed rate is the moment a line stops making money, and nothing
        said so before.

        The estimate itself is never rewritten. It holds the rates it was
        approved at, and a frozen cost plan moving because a subcontract was
        placed months later is exactly what the build-up snapshots exist to
        prevent. The comparison is reported here and in the cost variance
        report instead.
        """
        action = action_for("subcontract_above_cost_action")
        if action == "Ignore":
            return
        over = []
        for row in self.items:
            if not row.item_code or not flt(row.boq_cost_rate):
                continue
            if flt(row.rate) > flt(row.boq_cost_rate) + 0.005:
                over.append(row)
        if not over:
            return
        enforce(
            action,
            "<br>".join(
                _("Row {0} ({1}): agreed at {2}, the estimate costed it at {3}").format(
                    r.idx, r.work_no or r.item_code, flt(r.rate), flt(r.boq_cost_rate)
                )
                for r in over[:10]
            ),
            title=_("{0} line(s) above the costed rate").format(len(over)),
        )

    def check_against_the_library(self):
        """The rate agreed, against what the rate library costed the item at.

        Asked here, on save, because the purchase order that used to ask it is
        only raised on submit — and a warning about a commitment already made
        is not a warning, it is a note.
        """
        from construction_management_suite.project_costing.purchase_controls import (
            warn_above_estimated_rate,
        )

        warn_above_estimated_rate(self, self.items)

    def check_against_budget(self):
        """Ask the budget question here, where it can still be answered.

        It was asked by the purchase order this agreement raises, which happens
        on submit — so the warning arrived after the commitment, and the only
        way to act on it was to cancel.
        """
        from construction_management_suite.project_costing.purchase_controls import (
            warn_if_over_budget,
        )

        warn_if_over_budget(self, self.project, flt(self.subcontract_value))

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
        self.mark_finished()

    def mark_finished(self):
        """An agreement certified in full is finished, and so is its order.

        Left to a human this never happened: the status was not even editable
        once the agreement was signed, so every subcontract order on the site
        stayed open whatever had been certified against it.
        """
        if self.docstatus != 1 or self.status in ("Completed", "Terminated", "Cancelled"):
            return
        value = flt(self.subcontract_value)
        if not value or flt(self.total_certified) + 0.005 < value:
            return
        self.db_set("status", "Completed", update_modified=False)
        # The order is NOT closed here. The certificate has only just raised its
        # invoice, as a draft, and a closed order cannot be invoiced against —
        # closing it now makes the invoice unsubmittable. The order closes when
        # the last invoice against it is submitted; see
        # project_costing.purchase_controls.close_subcontract_order.

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

    def on_update_after_submit(self):
        self.close_order_when_finished()

    def close_order_when_finished(self):
        """Finish the order when the agreement is finished.

        A subcontract order is for a service, and a service cannot be received
        — `per_received` never moves, so even an order billed to the last fils
        sits at "To Receive" for ever. ERPNext's own answer to that is to close
        it, which is what an agreement reaching Completed or Terminated means.

        This is the path for an agreement that ends with work uncertified; one
        certified in full closes its order as the last invoice is submitted.

        Only ever closes; re-opening one is a decision for whoever reopens the
        agreement, and ERPNext has a button for it.
        """
        if self.status not in ("Completed", "Terminated"):
            return
        order = self.purchase_order_ref
        if not order:
            return
        state = frappe.db.get_value("Purchase Order", order, ["docstatus", "status"], as_dict=True)
        if not state or state.docstatus != 1 or state.status in ("Closed", "Cancelled"):
            return
        # A closed order refuses to be invoiced, so never close one with an
        # invoice still waiting to be submitted against it.
        if frappe.db.exists(
            "Purchase Invoice Item", {"purchase_order": order, "docstatus": 0}
        ):
            frappe.msgprint(
                _("{0} is left open: an unsubmitted invoice still bills against it.")
                .format(order),
                alert=True,
            )
            return
        frappe.get_doc("Purchase Order", order).update_status("Closed")
        frappe.msgprint(
            _("Purchase Order {0} closed — this agreement is {1}.").format(order, _(self.status)),
            alert=True,
        )

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
        store = self.warehouse or get_warehouse(self.project, self.company)
        for item in self.items:
            line = {
                "item_code": item.item_code,
                "item_name": item.description,
                "description": item.description,
                "qty": item.qty,
                "uom": item.uom,
                "rate": item.rate,
                "project": self.project,
                "cost_center": cost_center,
                "schedule_date": po.schedule_date,
                # The line of work this order is against. It rides from here to
                # the receipt and the invoice on its own, because the field is
                # named the same on all of them — but only if it starts here.
                "cms_work_item": item.work_item or item.item_code,
            }
            # Letting a material to a trade — the plywood to a formwork
            # specialist — puts a stock item on the order, and ERPNext will not
            # order stock without saying where it lands. A service needs none.
            if item.item_code and frappe.db.get_value("Item", item.item_code, "is_stock_item"):
                line["warehouse"] = store
            po.append("items", line)
        # The tax the agreement was signed with is the tax the order is placed
        # at. The certificate path has always carried it; this one imported the
        # helper and never called it, so an agreed VAT reached the supplier's
        # order as nothing at all.
        carry_taxes(self, po, cost_center)
        # Written on the rows and left blank on the document, which is where
        # ERPNext shows it and where a buyer looks for it.
        stamp_accounting(po, project=self.project, cost_center=cost_center)
        po.insert(ignore_permissions=True)
        self.db_set("purchase_order_ref", po.name)
        frappe.msgprint(_("Purchase Order {0} created").format(po.name))
