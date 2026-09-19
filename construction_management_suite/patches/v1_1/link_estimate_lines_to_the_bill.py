"""Point existing Cost Estimation lines at the bill line they came from.

The take-off now reads the Cost Estimation, and labels each material with the
bill item number the work sits under — 1.3 rather than CMS-RCC-M30. Estimates
made before `boq_item_ref` existed carry neither, so their take-off falls back
to the item code and the consumption picker offers rows in item-code order
instead of bill order.

Matched through the estimate's own `boq_ref` by item code, in row order, so a
bill that lists the same item twice still lines up. An estimate that names no
BOQ, or a line with no match, is left alone — a wrong link would be worse than
none, and the fallback still reads sensibly.
"""

import frappe


def execute():
    meta = frappe.get_meta("Cost Estimation Item")
    if not (meta.has_field("boq_item_ref") and meta.has_field("boq_item_no")):
        return

    linked = 0
    for est in frappe.get_all(
        "Cost Estimation", filters={"docstatus": ["<", 2]}, fields=["name", "boq_ref"]
    ):
        if not est.boq_ref or not frappe.db.exists("BOQ", est.boq_ref):
            continue

        # Bill lines for this BOQ, grouped by item and kept in the bill's order.
        available = {}
        for row in frappe.get_all(
            "BOQ Item",
            filters={"parent": est.boq_ref},
            fields=["name", "item_code", "item_no"],
            order_by="idx asc",
        ):
            available.setdefault(row.item_code, []).append(row)

        for line in frappe.get_all(
            "Cost Estimation Item",
            filters={"parent": est.name, "boq_item_ref": ["in", ["", None]]},
            fields=["name", "item_code"],
            order_by="idx asc",
        ):
            candidates = available.get(line.item_code)
            if not candidates:
                continue
            match = candidates.pop(0)
            frappe.db.set_value(
                "Cost Estimation Item",
                line.name,
                {"boq_item_ref": match.name, "boq_item_no": match.item_no},
                update_modified=False,
            )
            linked += 1

    if linked:
        print(f"Construction: linked {linked} estimate line(s) to their bill line")
