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
        self.calculate_totals()
        self.set_status()

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
            if row.agreement_item_ref:
                mine[row.agreement_item_ref] = mine.get(row.agreement_item_ref, 0) + flt(row.contract_qty)
        rows = {r.agreement_item_ref: r for r in self.items if r.agreement_item_ref}

        added = topped = 0
        covered = []
        for line in get_agreement_lines(self.subcontract_agreement, work_order=self.name):
            ref = line["agreement_item_ref"]
            remaining = (
                flt(line["agreed_qty"])
                - flt(line["instructed_elsewhere"])
                - flt(mine.get(ref))
            )
            if remaining <= 0.0001:
                if line["instructed_on"]:
                    covered.append((line["description"], line["instructed_on"]))
                continue
            row = rows.get(ref)
            if row:
                row.contract_qty = flt(row.contract_qty) + remaining
                topped += 1
            else:
                self.append("items", {
                    "agreement_item_ref": ref,
                    "item_code": line["item_code"],
                    "boq_ref": line["boq_ref"],
                    "boq_item_no": line["boq_item_no"],
                    "description": line["description"],
                    "uom": line["uom"],
                    "contract_qty": remaining,
                    "contract_rate": line["contract_rate"],
                    "completed_qty": 0,
                })
                added += 1
        return {
            "added": added,
            "topped_up": topped,
            # Say WHERE the quantity went, rather than only that there is none.
            "covered_by": [
                {"description": d, "orders": o} for d, o in covered
            ],
        }

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
        for field in ("total_contract_value", "total_completed_value", "completion_percent", "status"):
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
