import frappe

# Exactly the columns this release removed, named one by one.
#
# NOT `bench trim-tables`: that drops every column absent from its DocType across
# the whole site, and on a bench running other apps it would take their orphans
# with it — on the site this was written against, Property Booking, Sales
# Agreement and PDC Entry columns holding live data.
DROPPED = {
    "Cost Estimation Item": ("boq_item_ref", "boq_item_no"),
    "IPC Item": ("boq_item_ref",),
    "Variation Order Item": ("boq_item_ref",),
    "Subcontract Item": ("boq_ref", "boq_item_no"),
    "Subcontractor Payment Item": ("boq_ref", "boq_item_no", "work_order_item_ref"),
    "Subcontractor Work Order Item": ("boq_ref", "boq_item_no", "agreement_item_ref"),
    "Material Forecast Item": ("boq_item_ref", "boq_item_no", "boq_items"),
    "Material Consumption Item": ("boq_item_ref", "boq_item_no"),
    "Site Transfer Item": ("cms_work_ref", "cms_work_no"),
    "Site Material Request Item": ("boq_item_ref", "boq_item_no"),
    "Site Report Activity": ("boq_item_ref", "boq_item_no"),
    "Material Request Item": ("cms_work_ref", "cms_work_no"),
    "Purchase Order Item": ("cms_work_ref", "cms_work_no"),
    "Purchase Receipt Item": ("cms_work_ref", "cms_work_no"),
    "Purchase Invoice Item": ("cms_work_ref", "cms_work_no"),
    "Stock Entry Detail": ("cms_work_ref", "cms_work_no"),
}


def execute():
    """Remove the row-name work references the Item-keyed model replaces.

    The old scheme carried a hidden child-row name (`*_ref`) plus a visible
    number (`*_no`) on every document. The row name did not survive a bill being
    revised — `create_revision` regenerates every row — so claimed-to-date reset
    to zero and the take-off lost its attribution. The work Item is the key now:
    it is stable across revisions, and it is a real Link.

    The app is not deployed anywhere, so these are dropped rather than migrated.
    """
    frappe.db.delete(
        "Custom Field", {"fieldname": ["in", ("cms_work_ref", "cms_work_no")]}
    )

    for doctype, columns in DROPPED.items():
        if not frappe.db.table_exists(doctype):
            continue
        present = set(frappe.db.get_table_columns(doctype))
        gone = [c for c in columns if c in present]
        if not gone:
            continue
        table = f"tab{doctype}"
        drops = ", ".join(f"DROP COLUMN `{c}`" for c in gone)
        frappe.db.sql_ddl(f"ALTER TABLE `{table}` {drops}")
        print(f"  dropped from {doctype}: {', '.join(gone)}")

    frappe.clear_cache()
