import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from construction_management_suite.utils.accounting import get_cost_center, get_warehouse
from construction_management_suite.utils.billing import stamp_accounting
from construction_management_suite.utils.titles import project_label, set_auto_title, short_date
from construction_management_suite.utils.validations import (
    validate_item_kinds,
    validate_project_company,
    validate_uom_convertible,
)


class SiteTransfer(Document):
    def validate(self):
        # Both ends, because which job it came off is half the story.
        set_auto_title(self, "transfer_title",
                       [" → ".join(p for p in (project_label(self.from_project),
                                               project_label(self.to_project)) if p),
                        short_date(self.transfer_date)])
        validate_project_company(self, ("from_project", "to_project"))
        validate_item_kinds(self.items)
        validate_uom_convertible(self.items)
        self.set_default_warehouses()
        if self.from_warehouse == self.to_warehouse:
            frappe.throw(_("Source and destination warehouses must be different"))
        self.set_work_numbers()

    def set_default_warehouses(self):
        """Each end's store from the project at that end, where it names one.

        Never where it would make the two ends equal: a default that raises the
        error the user was about to be saved from is worse than a blank field.
        """
        if not self.from_warehouse:
            store = get_warehouse(self.from_project, self.company)
            if store and store != self.to_warehouse:
                self.from_warehouse = store
        if not self.to_warehouse:
            store = get_warehouse(self.to_project, self.company)
            if store and store != self.from_warehouse:
                self.to_warehouse = store

    def set_work_numbers(self):
        """Fill the bill number beside each work item. Display only."""
        from construction_management_suite.material_planning.doctype.material_consumption_entry.material_consumption_entry import (
            work_no_map,
        )

        project = self.get("project") or self.get("to_project")
        numbers = work_no_map(project) if project else {}
        for row in self.items:
            row.work_no = numbers.get(row.work_item)


    @frappe.whitelist()
    def get_items_from_take_off(self):
        """Offer the receiving job's outstanding materials, per line of work.

        `cms_work_item` was passed to the Stock Entry from the first day and
        nothing ever filled it, so material moved onto a site was invisible to
        every per-work total. This is what fills it.
        """
        from construction_management_suite.api.boq import take_off_outstanding

        if not self.to_project:
            frappe.throw(_("Choose the project this transfer is going to"))

        on_form = {(i.item_code, i.work_item) for i in self.items if i.item_code}
        added = 0
        for line in take_off_outstanding(self.to_project):
            key = (line["item_code"], line["cms_work_item"])
            if key in on_form:
                continue
            self.append("items", {
                "item_code": line["item_code"],
                "uom": line["uom"],
                "qty": line["qty"],
                "work_item": line["cms_work_item"],
                "work_no": line["work_no"],
            })
            added += 1
        return added

    def before_submit(self):
        self.status = "Submitted"

    def on_submit(self):
        self._create_stock_entry()

    def before_cancel(self):
        self.status = "Cancelled"

    def on_cancel(self):
        self._cancel_stock_entry()

    def _cancel_stock_entry(self):
        if not self.stock_entry_ref:
            return
        se = frappe.get_doc("Stock Entry", self.stock_entry_ref)
        if se.docstatus == 1:
            se.cancel()
            frappe.msgprint(_("Stock Entry {0} cancelled").format(se.name))

    def _create_stock_entry(self):
        se = frappe.new_doc("Stock Entry")
        se.stock_entry_type = "Material Transfer"
        se.company = self.company
        se.posting_date = self.transfer_date
        se.cms_site_ref = self.name
        if self.to_project:
            se.project = self.to_project
        cost_center = get_cost_center(self.to_project, self.company)
        for item in self.items:
            se.append("items", {
                "item_code": item.item_code,
                "qty": flt(item.qty),
                "uom": item.uom,
                "s_warehouse": self.from_warehouse,
                "t_warehouse": self.to_warehouse,
                "batch_no": item.batch_no,
                "serial_no": item.serial_no,
                # See Material Consumption Entry: the plain fields only apply
                # when the row says so, whatever Stock Settings happens to say.
                "use_serial_batch_fields": 1,
                "cost_center": cost_center,
                "project": self.to_project,
                "cms_work_item": item.get("work_item"),
            })
        stamp_accounting(se, project=self.to_project, cost_center=cost_center)
        se.insert(ignore_permissions=True)
        se.submit()
        self.db_set("stock_entry_ref", se.name)
        frappe.msgprint(_("Stock Entry {0} created and submitted").format(se.name))
