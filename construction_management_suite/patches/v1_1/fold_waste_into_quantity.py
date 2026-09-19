"""Fold each resource's waste factor into its quantity, then retire the factor.

A waste percentage on a Rate Analysis resource looked like a convenience and
worked as a trap: the same allowance could be written twice — once in the
quantity a quantity surveyor had already grossed up, once in the field — and
every figure downstream carried it silently. In practice the allowance is made
before the quantity is typed, so the field earns nothing and costs accuracy.

Folding rather than dropping, because dropping moves real numbers. Multiplying
the quantity by (1 + waste/100) leaves the cost identical — the old net_amount
becomes the new amount — and leaves the take-off identical, because the
quantity per unit now carries the allowance it always represented. On this
site that is 21 resource rows, 8 forecast rows and every one of the 11 frozen
build-ups on priced lines.

The frozen build-ups matter most: the take-off reads them before it reads the
library, so a snapshot left un-folded would quietly shed its waste — 8,059.8
bags of cement becoming 7,676.

Reads `waste_factor` through raw SQL: the column survives the field being
removed from the doctype, because Frappe's schema sync never drops one.
"""

import json

import frappe
from frappe.utils import flt


def execute():
    fold_library()
    fold_frozen_build_ups()


def fold_library():
    rows = frappe.db.sql(
        """SELECT name, qty, rate, waste_factor FROM `tabRate Analysis Resource`
           WHERE IFNULL(waste_factor, 0) != 0""",
        as_dict=True,
    )
    for row in rows:
        qty = flt(row.qty) * (1 + flt(row.waste_factor) / 100)
        amount = qty * flt(row.rate)
        # net_amount is going with the factor; keep it equal meanwhile so a
        # half-migrated site never shows two different costs for one row.
        frappe.db.sql(
            """UPDATE `tabRate Analysis Resource`
               SET qty = %s, amount = %s, net_amount = %s, waste_factor = 0
               WHERE name = %s""",
            (qty, amount, amount, row.name),
        )
    if rows:
        print(f"Construction: folded waste into {len(rows)} rate analysis resource row(s)")

    # A forecast row already stores the waste-inclusive figure in
    # net_qty_required, so only the factor itself has to go.
    frappe.db.sql("UPDATE `tabMaterial Forecast Item` SET waste_factor = 0 WHERE IFNULL(waste_factor, 0) != 0")


def fold_frozen_build_ups():
    folded = 0
    for table in ("tabBOQ Item", "tabCost Estimation Item"):
        rows = frappe.db.sql(
            f"SELECT name, rate_build_up FROM `{table}` WHERE IFNULL(rate_build_up, '') != ''",
            as_dict=True,
        )
        for row in rows:
            try:
                frozen = json.loads(row.rate_build_up)
            except (ValueError, TypeError):
                continue
            changed = False
            for res in frozen.get("resources") or []:
                waste = flt(res.pop("waste_factor", 0))
                if waste:
                    res["qty"] = flt(res.get("qty")) * (1 + waste / 100)
                    changed = True
                # net_amount already carried the waste, so it is the new amount.
                if "net_amount" in res:
                    res["amount"] = flt(res.pop("net_amount"))
                    changed = True
            if not changed:
                continue
            frappe.db.set_value(
                table.replace("tab", "", 1), row.name, "rate_build_up",
                json.dumps(frozen, default=str), update_modified=False,
            )
            folded += 1
    if folded:
        print(f"Construction: folded waste into {folded} frozen rate build-up(s)")
