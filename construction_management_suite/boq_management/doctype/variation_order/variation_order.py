import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate
from construction_management_suite.utils.validations import validate_project_company
from construction_management_suite.utils.titles import (
    month_of,
    project_label,
    set_auto_title,
)


class VariationOrder(Document):
    """A formal change to the contract scope after signing.

    Additions increase the contract sum, omissions reduce it. The net is applied
    to the project's contract value on approval, so the revised sum always
    reflects every approved variation.
    """

    def validate(self):
        validate_project_company(self)
        self.set_vo_number()
        set_auto_title(self, "vo_title", [_("VO #{0}").format(self.vo_number) if self.vo_number else _("Variation"), project_label(self.project), self.variation_type])
        self.calculate_items()
        self.calculate_totals()
        self.set_contract_position()

    @frappe.whitelist()
    def add_boq_lines(self, rows):
        """Append the chosen BOQ lines, linked to the rows they vary."""
        import json as _json

        if isinstance(rows, str):
            rows = _json.loads(rows)
        existing = {i.boq_item_ref for i in self.items if i.boq_item_ref}
        added = 0
        for row in rows:
            if row.get("boq_item_ref") in existing:
                continue
            row.setdefault("nature", "Omission")
            self.append("items", row)
            added += 1
        return added

    def set_vo_number(self):
        """Number variations in sequence per project.

        Typed by hand it drifts, and the number is what both sides quote in
        correspondence for the life of the contract.
        """
        if self.vo_number or not self.project:
            return
        last = frappe.db.sql(
            """SELECT MAX(vo_number) FROM `tabVariation Order`
               WHERE project = %s AND docstatus < 2 AND name != %s""",
            (self.project, self.name or ""),
        )
        self.vo_number = int(flt(last[0][0])) + 1 if last and last[0][0] else 1

    def before_submit(self):
        self.status = "Approved"
        if not self.approved_by:
            self.approved_by = frappe.session.user
        if not self.approval_date:
            self.approval_date = nowdate()

    def on_submit(self):
        self._apply_to_project(flt(self.net_variation_amount))

    def before_cancel(self):
        # before, not on_cancel: on_cancel runs after the row is written.
        self.status = "Cancelled"

    def on_cancel(self):
        self._apply_to_project(-flt(self.net_variation_amount))

    # ----- Calculations -----

    def calculate_items(self):
        for row in self.items:
            row.amount = flt(row.qty) * flt(row.rate)
            if flt(row.qty) < 0:
                frappe.throw(
                    _("Row {0}: Quantity cannot be negative — use Nature 'Omission' instead").format(row.idx)
                )

    def calculate_totals(self):
        self.addition_amount = sum(flt(r.amount) for r in self.items if r.nature == "Addition")
        self.omission_amount = sum(flt(r.amount) for r in self.items if r.nature == "Omission")
        self.net_variation_amount = flt(self.addition_amount) - flt(self.omission_amount)

    def set_contract_position(self):
        """Show what this variation does to the contract sum."""
        self.original_contract_value = self._original_contract_value()
        self.previous_variations_amount = self._previous_variations()
        self.revised_contract_value = (
            flt(self.original_contract_value)
            + flt(self.previous_variations_amount)
            + flt(self.net_variation_amount)
        )

    def _original_contract_value(self):
        """The contract sum before any variation, taken from the BOQ if linked."""
        if self.boq_ref:
            value = frappe.db.get_value("BOQ", self.boq_ref, "grand_total")
            if value:
                return flt(value)
        if self.project:
            return flt(frappe.db.get_value("Project", self.project, "cms_contract_value"))
        return 0.0

    def _previous_variations(self):
        """Net of every other approved variation on this project."""
        if not self.project:
            return 0.0
        total = frappe.db.sql(
            """
            SELECT SUM(net_variation_amount)
            FROM `tabVariation Order`
            WHERE project = %s AND docstatus = 1 AND name != %s
            """,
            (self.project, self.name or ""),
        )[0][0]
        return flt(total)

    # ----- ERPNext Integration -----

    def _apply_to_project(self, delta):
        """Move the project's contract value by the net variation."""
        if not (self.project and delta):
            return
        current = flt(frappe.db.get_value("Project", self.project, "cms_contract_value"))
        frappe.db.set_value("Project", self.project, "cms_contract_value", current + delta)
        frappe.msgprint(
            _("Contract value for {0} adjusted by {1} to {2}").format(
                self.project,
                frappe.format_value(delta, {"fieldtype": "Currency", "options": "currency"}, self),
                frappe.format_value(current + delta, {"fieldtype": "Currency", "options": "currency"}, self),
            )
        )
