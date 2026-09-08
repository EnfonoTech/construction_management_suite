import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from construction_management_suite.utils.validations import validate_project_company


class SiteMaterialRequest(Document):
    """What site is asking stores or procurement for.

    On submit it becomes a draft ERPNext Material Request so buyers work from
    their normal queue rather than a separate list.
    """

    def validate(self):
        validate_project_company(self)
        self.validate_quantities()

    def validate_quantities(self):
        for row in self.items:
            if flt(row.qty_requested) <= 0:
                frappe.throw(
                    _("Row {0}: Requested quantity must be greater than zero").format(row.idx)
                )

    def on_submit(self):
        self.status = "Pending Approval"
        self._create_material_request()

    def on_cancel(self):
        self.status = "Rejected"
        self._cancel_material_request()

    def _create_material_request(self):
        if self.material_request_ref:
            return

        mr = frappe.new_doc("Material Request")
        # Site asks for material; procurement decides whether that is met from
        # stock or bought in, so raise it as a Purchase request by default.
        mr.material_request_type = "Purchase"
        mr.company = self.company
        mr.transaction_date = self.request_date
        mr.schedule_date = self.required_date or frappe.utils.add_days(self.request_date, 7)

        for item in self.items:
            qty = flt(item.qty_approved) or flt(item.qty_requested)
            if qty <= 0:
                continue
            mr.append("items", {
                "item_code": item.item_code,
                "qty": qty,
                "uom": item.uom,
                "warehouse": item.warehouse,
                "project": self.project,
                "schedule_date": self.required_date or mr.schedule_date,
                "description": item.description or item.item_name,
            })

        if not mr.items:
            frappe.throw(_("Nothing to request — every row has zero quantity"))

        mr.insert(ignore_permissions=True)
        self.db_set("material_request_ref", mr.name)
        frappe.msgprint(_("Material Request {0} created").format(mr.name))

    def _cancel_material_request(self):
        if not self.material_request_ref:
            return
        mr = frappe.get_doc("Material Request", self.material_request_ref)
        if mr.docstatus == 1:
            mr.cancel()
