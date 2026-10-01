"""Fill `work_item` on subcontract lines that predate the field.

A subcontract line now says two things, the way a material line always has:
which line of work it belongs to, and what is actually being let. Before the
field existed it could only say one, so `item_code` carried the line of work
and a subcontract priced *inside* a line could not be expressed at all —
the agreement had to name the whole work item, and then billed and reported as
though the entire line had been let.

Every existing row was therefore a whole line of work, and that is what this
says: the work is the item. Rows already carrying a work reference, and rows
whose item is not on the project's estimate, are left alone.
"""

import frappe

TABLES = (
    ("Subcontract Item", "Subcontract Agreement"),
    ("Subcontractor Work Order Item", "Subcontractor Work Order"),
    ("Subcontractor Payment Item", "Subcontractor Payment Certificate"),
)


def execute():
    for child, parent in TABLES:
        if not frappe.db.has_column(child, "work_item"):
            continue
        rows = frappe.db.sql(
            f"""
            SELECT c.name, c.item_code
            FROM `tab{child}` c
            WHERE IFNULL(c.work_item, '') = '' AND IFNULL(c.item_code, '') != ''
            """,
            as_dict=True,
        )
        for row in rows:
            frappe.db.set_value(child, row.name, "work_item", row.item_code,
                                update_modified=False)
        if rows:
            print(f"Construction: {len(rows)} {child} row(s) given their work item")
