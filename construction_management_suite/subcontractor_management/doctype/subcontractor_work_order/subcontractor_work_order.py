import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt
from construction_management_suite.utils.validations import validate_project_company
from construction_management_suite.utils.titles import (
    month_of,
    project_label,
    set_auto_title,
)


class SubcontractorWorkOrder(Document):
    def before_cancel(self):
        # before, not on_cancel: on_cancel runs after the row is written.
        self.status = "Cancelled"

    def validate(self):
        set_auto_title(self, "work_order_title", [_("Work Order"), self.subcontractor, project_label(self.project)])
        validate_project_company(self)
        self.set_work_numbers()
        self.calculate_totals()
        self.set_status()

    def set_work_numbers(self):
        """Fill the bill number beside each work item. Display only.

        Filled only when the scope was pulled from the agreement, so an order
        whose lines were typed showed nothing — and the certificate raised from
        it inherited the blank.
        """
        from construction_management_suite.material_planning.doctype.material_consumption_entry.material_consumption_entry import (
            work_no_map,
        )

        numbers = work_no_map(self.project) if self.project else {}
        for row in self.items:
            row.work_no = numbers.get(row.item_code)

    @frappe.whitelist()
    def get_scope_from_agreement(self):
        """Pull the part of the agreement not yet instructed.

        What is already on THIS order is netted off from `self.items` rather
        than from the database, because the form sends its in-memory document
        and the rows on screen are the ones that matter.
        """
        from construction_management_suite.api.boq import get_agreement_lines

        if not self.subcontract_agreement:
            frappe.throw(_("Choose the agreement this order releases"))

        mine = {}
        for row in self.items:
            if row.item_code:
                mine[row.item_code] = mine.get(row.item_code, 0) + flt(row.contract_qty)
        rows = {r.item_code: r for r in self.items if r.item_code}

        added = topped = 0
        open_elsewhere = []
        for line in get_agreement_lines(self.subcontract_agreement, work_order=self.name):
            ref = line["item_code"]
            remaining = (
                flt(line["agreed_qty"])
                - flt(line["delivered_elsewhere"])
                - flt(mine.get(ref))
            )
            if remaining <= 0.0001:
                continue
            if line["open_on"]:
                # Offered, but say so: another order still has this quantity on
                # its books, and instructing it twice is a real mistake.
                open_elsewhere.append({
                    "description": line["description"],
                    "orders": line["open_on"],
                })
            row = rows.get(ref)
            if row:
                row.contract_qty = flt(row.contract_qty) + remaining
                topped += 1
            else:
                self.append("items", {
                    "item_code": line["item_code"],
                    "work_no": line["work_no"],
                    "description": line["description"],
                    "uom": line["uom"],
                    "contract_qty": remaining,
                    "contract_rate": line["contract_rate"],
                    "completed_qty": 0,
                })
                added += 1
        return {"added": added, "topped_up": topped, "open_elsewhere": open_elsewhere}

    def on_update_after_submit(self):
        """Recompute and PERSIST after progress is recorded.

        update_after_submit writes only the allow_on_submit fields the client
        sent; anything derived here lives in memory until it is written, which
        is why the totals read zero while the rows held real quantities.
        """
        self.calculate_totals()
        self.set_status()
        for row in self.items:
            row.db_set("completed_amount", flt(row.completed_amount), update_modified=False)
            row.db_set("completion_percent", flt(row.completion_percent), update_modified=False)
        for field in ("total_contract_value", "total_completed_value",
                      "completion_percent", "status"):
            self.db_set(field, self.get(field), update_modified=False)

    def set_status(self):
        """Draft, Issued, In Progress, Completed — the options existed and
        nothing ever moved between them, so every submitted order read Draft and
        a closed order was indistinguishable from an open one."""
        if self.docstatus == 2:
            self.status = "Cancelled"
            return
        if self.docstatus == 0:
            self.status = "Draft"
            return
        if self.status in ("Completed", "Cancelled"):
            return
        pct = flt(self.completion_percent)
        self.status = "Completed" if pct >= 99.995 else ("In Progress" if pct > 0 else "Issued")

    def calculate_totals(self):
        total_contract = 0
        total_completed = 0
        for item in self.items:
            item.contract_amount = flt(item.contract_qty) * flt(item.contract_rate)
            item.completed_amount = flt(item.completed_qty) * flt(item.contract_rate)
            if flt(item.contract_qty) > 0:
                item.completion_percent = flt(item.completed_qty) / flt(item.contract_qty) * 100
            total_contract += flt(item.contract_amount)
            total_completed += flt(item.completed_amount)
        self.total_contract_value = total_contract
        self.total_completed_value = total_completed
        if flt(total_contract) > 0:
            self.completion_percent = flt(total_completed) / flt(total_contract) * 100
