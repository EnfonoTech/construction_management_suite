"""Move existing work orders off the status they were stuck on.

Draft / Issued / In Progress / Completed existed as options and nothing ever set
them, so every submitted order read "Draft" — including on the message that
tells you where a quantity went, which then looked simply wrong.
"""

import frappe
from frappe.utils import flt


def execute():
    moved = 0
    for w in frappe.get_all(
        "Subcontractor Work Order",
        fields=["name", "docstatus", "status", "completion_percent"],
    ):
        if w.docstatus == 2:
            status = "Cancelled"
        elif w.docstatus == 0:
            status = "Draft"
        elif w.status in ("Completed", "Cancelled"):
            continue
        else:
            pct = flt(w.completion_percent)
            status = "Completed" if pct >= 99.995 else ("In Progress" if pct > 0 else "Issued")
        if status != w.status:
            frappe.db.set_value("Subcontractor Work Order", w.name, "status", status,
                                update_modified=False)
            moved += 1
    if moved:
        print(f"Construction: moved {moved} work order(s) off a stale status")
