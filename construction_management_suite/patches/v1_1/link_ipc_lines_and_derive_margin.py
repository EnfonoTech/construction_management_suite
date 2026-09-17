"""Bring existing records onto the new meaning of four fields.

Three separate shifts land in one patch because they are one story: an IPC line
now points at the BOQ *row* it certifies, which is what lets certified quantity
flow back to the bill, which is what makes BOQ variance a real number instead of
the flat negative it showed while nothing ever wrote `actual_qty`.
"""

import frappe
from frappe.utils import flt


def execute():
    link_ipc_lines_to_boq_rows()
    backfill_certified_qty()
    derive_boq_margin()
    seed_rate_analysis_flags()


def link_ipc_lines_to_boq_rows():
    """`boq_item_ref` used to hold an item code; it now holds a BOQ Item row name.

    The old value moves to the new `item_code` column, and the row name is
    resolved through the certificate's own BOQ. A line whose item appears twice
    in one bill cannot be resolved without guessing which was meant, so it is
    left unlinked and reported rather than attached to the wrong one.
    """
    rows = frappe.db.sql(
        """
        SELECT i.name, i.boq_item_ref, p.boq_ref
        FROM `tabIPC Item` i
        JOIN `tabInterim Payment Certificate` p ON p.name = i.parent
        WHERE i.boq_item_ref IS NOT NULL AND i.boq_item_ref != ''
        """,
        as_dict=True,
    )
    ambiguous, unresolved = [], []
    for row in rows:
        # Already a row name (re-run, or written by the new code).
        if frappe.db.exists("BOQ Item", row.boq_item_ref):
            continue

        frappe.db.set_value("IPC Item", row.name, "item_code", row.boq_item_ref,
                            update_modified=False)
        if not row.boq_ref:
            unresolved.append(row.name)
            continue

        matches = frappe.get_all(
            "BOQ Item",
            filters={"parent": row.boq_ref, "item_code": row.boq_item_ref},
            pluck="name",
        )
        if len(matches) == 1:
            frappe.db.set_value("IPC Item", row.name, "boq_item_ref", matches[0],
                                update_modified=False)
        elif len(matches) > 1:
            ambiguous.append((row.name, row.boq_item_ref))
        else:
            unresolved.append(row.name)

    if ambiguous or unresolved:
        print(
            f"Construction: {len(ambiguous)} IPC line(s) matched more than one BOQ row and "
            f"{len(unresolved)} matched none — link these by hand: "
            f"{[a[1] for a in ambiguous] + unresolved}"
        )


def backfill_certified_qty():
    """Set every BOQ line's actual quantity from the certificates already signed."""
    certified = frappe.db.sql(
        """
        SELECT i.boq_item_ref AS ref, SUM(i.qty_this_period) AS qty
        FROM `tabIPC Item` i
        JOIN `tabInterim Payment Certificate` p ON p.name = i.parent
        WHERE p.docstatus = 1 AND i.boq_item_ref IS NOT NULL AND i.boq_item_ref != ''
        GROUP BY i.boq_item_ref
        """,
        as_dict=True,
    )
    certified = {r.ref: flt(r.qty) for r in certified}

    for row in frappe.get_all("BOQ Item", fields=["name", "qty", "rate"]):
        actual = flt(certified.get(row.name))
        variance_qty = actual - flt(row.qty)
        frappe.db.set_value(
            "BOQ Item",
            row.name,
            {
                "actual_qty": actual,
                "variance_qty": variance_qty,
                "variance_amount": variance_qty * flt(row.rate),
            },
            update_modified=False,
        )


def derive_boq_margin():
    """`margin_percent` drove the rate; now it reports on it."""
    for row in frappe.get_all("BOQ Item", fields=["name", "qty", "rate", "cost_rate"]):
        cost = flt(row.cost_rate)
        frappe.db.set_value(
            "BOQ Item",
            row.name,
            {
                "margin_percent": ((flt(row.rate) - cost) / cost * 100) if cost else 0,
                "margin_amount": (flt(row.rate) - cost) * flt(row.qty),
            },
            update_modified=False,
        )


def seed_rate_analysis_flags():
    """Every existing analysis is active; an item with exactly one becomes its default.

    Only the unambiguous case is decided here. Where an item has several approved
    analyses, which one prices new work is a commercial judgement, so the field is
    left blank for the estimator rather than guessed from a timestamp.
    """
    frappe.db.sql("UPDATE `tabRate Analysis` SET is_active = 1 WHERE status != 'Obsolete'")
    frappe.db.sql("UPDATE `tabRate Analysis` SET is_active = 0 WHERE status = 'Obsolete'")

    by_item = {}
    for ra in frappe.get_all(
        "Rate Analysis",
        filters={"status": "Approved", "is_active": 1, "item_code": ("is", "set")},
        fields=["name", "item_code"],
    ):
        by_item.setdefault(ra.item_code, []).append(ra.name)

    for item_code, names in by_item.items():
        if len(names) == 1:
            frappe.db.set_value("Rate Analysis", names[0], "is_default", 1,
                                update_modified=False)
        else:
            print(
                f"Construction: {item_code} has {len(names)} approved analyses "
                f"({', '.join(names)}) — tick Is Default on the one to price from."
            )
