import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate
from construction_management_suite.utils.validations import validate_project_company


class BOQ(Document):
    # ----- Lifecycle -----

    def validate(self):
        validate_project_company(self)
        self.set_currency_from_project()
        self.calculate_item_amounts()
        self.calculate_totals()
        self.validate_items()

    def before_submit(self):
        self.status = "Submitted"

    def on_submit(self):
        self._update_project_boq_link()

    def on_cancel(self):
        self.status = "Cancelled"
        self._update_project_boq_link()

    def on_update_after_submit(self):
        self.calculate_totals()

    def before_save(self):
        if not self.prepared_by:
            self.prepared_by = frappe.session.user

    # ----- Calculations -----

    def set_currency_from_project(self):
        if self.project and not self.currency:
            project_currency = frappe.db.get_value("Project", self.project, "currency")
            if project_currency:
                self.currency = project_currency

    def calculate_item_amounts(self):
        for item in self.items:
            item.cost_rate = (
                flt(item.material_rate) + flt(item.labour_rate) + flt(item.equipment_rate)
                + flt(item.subcontract_rate) + flt(item.overhead_rate)
            )
            # A line that carries its own margin is priced from its cost. Contractors
            # front-load deliberately — high margin on early work, thin on the tail —
            # so this has to be per line, not one figure for the whole bill.
            if flt(item.margin_percent) and flt(item.cost_rate):
                item.rate = flt(item.cost_rate) * (1 + flt(item.margin_percent) / 100)
            item.amount = flt(item.qty) * flt(item.rate)
            item.material_amount = flt(item.qty) * flt(item.material_rate)
            item.labour_amount = flt(item.qty) * flt(item.labour_rate)
            item.equipment_amount = flt(item.qty) * flt(item.equipment_rate)
            item.subcontract_amount = flt(item.qty) * flt(item.subcontract_rate)
            item.overhead_amount = flt(item.qty) * flt(item.overhead_rate)
            item.variance_qty = flt(item.actual_qty) - flt(item.qty)
            item.variance_amount = flt(item.variance_qty) * flt(item.rate)

    def calculate_totals(self):
        self.total_material_amount = sum(flt(i.material_amount) for i in self.items)
        self.total_labour_amount = sum(flt(i.labour_amount) for i in self.items)
        self.total_equipment_amount = sum(flt(i.equipment_amount) for i in self.items)
        self.total_subcontract_amount = sum(flt(i.subcontract_amount) for i in self.items)
        self.total_overhead_amount = sum(flt(i.overhead_amount) for i in self.items)
        self.total_amount = sum(flt(i.amount) for i in self.items)
        self.total_cost_amount = sum(flt(i.qty) * flt(i.cost_rate) for i in self.items)
        self.effective_margin_percent = (
            (flt(self.total_amount) - flt(self.total_cost_amount)) / flt(self.total_cost_amount) * 100
            if flt(self.total_cost_amount) else 0
        )
        self.profit_margin_amount = flt(self.total_amount) * flt(self.profit_margin_percent) / 100
        self.grand_total = flt(self.total_amount) + flt(self.profit_margin_amount)

    def validate_items(self):
        for row in self.items:
            if flt(row.qty) <= 0:
                frappe.throw(_("Row {0}: Quantity must be greater than zero").format(row.idx))
            if flt(row.rate) < 0:
                frappe.throw(_("Row {0}: Rate cannot be negative").format(row.idx))

    # ----- Revision Workflow -----

    @frappe.whitelist()
    def create_revision(self):
        """Create a revised BOQ copying all items — increments revision_no."""
        if self.docstatus != 1:
            frappe.throw(_("BOQ must be submitted before creating a revision"))

        new_boq = frappe.copy_doc(self)
        new_boq.docstatus = 0
        new_boq.status = "Draft"
        new_boq.revision_no = flt(self.revision_no) + 1
        new_boq.is_revised = 1
        # A revision is not an amendment — `amended_from` is reserved by Frappe for
        # cancelled documents and would be rejected on insert.
        new_boq.amended_from = None
        new_boq.previous_boq = self.name
        new_boq.approved_date = None
        new_boq.insert(ignore_permissions=True)

        self.db_set("status", "Revised")
        frappe.msgprint(_("Revised BOQ {0} created").format(new_boq.name))
        return new_boq.name

    # ----- ERPNext Integration -----

    def _update_project_boq_link(self):
        """Store latest active BOQ reference on the ERPNext Project."""
        if self.project:
            frappe.db.set_value("Project", self.project, "notes", self._build_project_notes())

    def _build_project_notes(self):
        import re

        existing = frappe.db.get_value("Project", self.project, "notes") or ""
        marker = "<!-- cms_boq -->"
        block = (
            f"{marker}\nBOQ: {self.name} | Grand Total: {self.grand_total} "
            f"{self.currency}\n{marker}"
        )
        if marker in existing:
            # The block is delimited by a matched pair of markers.
            return re.sub(
                re.escape(marker) + ".*?" + re.escape(marker), block, existing, flags=re.DOTALL
            )
        return existing + block

    # ----- Template Import -----

    @frappe.whitelist()
    def import_from_template(self, template_name):
        """Populate items from a BOQ Template."""
        template = frappe.get_doc("BOQ Template", template_name)
        for t_item in template.items:
            self.append("items", {
                "item_code": t_item.item_code,
                "description": t_item.description,
                "uom": t_item.uom,
                "qty": t_item.qty,
                "rate": t_item.rate,
                "work_category": t_item.work_category,
                "boq_section": t_item.boq_section,
            })
        self.calculate_item_amounts()
        self.calculate_totals()
