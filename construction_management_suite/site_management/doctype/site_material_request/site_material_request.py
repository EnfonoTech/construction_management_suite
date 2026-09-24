import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from construction_management_suite.utils.accounting import get_warehouse
from construction_management_suite.utils.validations import (
    validate_item_kinds,
    validate_project_company,
)


class SiteMaterialRequest(Document):
    """What site is asking stores or procurement for.

    On submit it becomes a draft ERPNext Material Request so buyers work from
    their normal queue rather than a separate list.
    """

    def validate(self):
        validate_project_company(self)
        validate_item_kinds(self.items)
        self.validate_quantities()
        self.set_default_warehouse()
        self.set_work_numbers()

    def set_default_warehouse(self):
        """The job's store on every row that does not name one."""
        store = get_warehouse(self.project, self.company)
        if not store:
            return
        for row in self.items:
            if not row.warehouse:
                row.warehouse = store

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
        """Offer what the estimate still needs bought, per material per work item.

        The site used to type what it wanted, so a request never met the plan in
        any report and nothing netted it against what was already on order.
        """
        from construction_management_suite.api.boq import take_off_outstanding

        if not self.project:
            frappe.throw(_("Choose the project this request is for"))

        on_form = {(i.item_code, i.work_item) for i in self.items if i.item_code}
        added = 0
        for line in take_off_outstanding(self.project):
            key = (line["item_code"], line["cms_work_item"])
            if key in on_form:
                continue
            self.append("items", {
                "item_code": line["item_code"],
                "uom": line["uom"],
                "qty_requested": line["qty"],
                "work_item": line["cms_work_item"],
                "work_no": line["work_no"],
            })
            added += 1
        return added

    def validate_quantities(self):
        for row in self.items:
            if flt(row.qty_requested) <= 0:
                frappe.throw(
                    _("Row {0}: Requested quantity must be greater than zero").format(row.idx)
                )

    def before_submit(self):
        self.status = "Pending Approval"

    def on_submit(self):
        self._create_material_request()

    def before_cancel(self):
        self.status = "Rejected"

    def on_cancel(self):
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
                # Rides on to the order, the receipt and the invoice by itself:
                # frappe's mapper copies fields of the same name.
                "cms_work_item": item.work_item,
            })

        if not mr.items:
            frappe.throw(_("Nothing to request — every row has zero quantity"))

        mr.cms_site_request_ref = self.name
        mr.insert(ignore_permissions=True)
        # Submitted, unlike the invoices and orders this app raises: those are
        # accounting events a human signs off, a request is not. Left as a draft
        # it sat in nobody's queue and counted in no report.
        mr.submit()
        self.db_set("material_request_ref", mr.name)
        frappe.msgprint(_("Material Request {0} submitted").format(mr.name))

    def _cancel_material_request(self):
        """Take the request with it — cancelled if live, deleted if still a draft.

        A draft was left behind and still linked, so the site request looked
        cancelled while a request for the same material sat in the buyer's list.
        """
        if not self.material_request_ref:
            return
        if not frappe.db.exists("Material Request", self.material_request_ref):
            return
        mr = frappe.get_doc("Material Request", self.material_request_ref)
        if mr.docstatus == 1:
            mr.cancel()
            frappe.msgprint(_("Material Request {0} cancelled").format(mr.name))
        elif mr.docstatus == 0:
            mr.delete(ignore_permissions=True)
            self.db_set("material_request_ref", None)
            frappe.msgprint(_("Draft Material Request {0} deleted").format(mr.name))
