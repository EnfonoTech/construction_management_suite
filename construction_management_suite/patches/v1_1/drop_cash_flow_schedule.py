"""Remove the Cash Flow Schedule that never had anything behind it.

`Project Cash Flow Item` was a real child table on every Project Budget — a
month, planned in and out, actual in and out, a variance — and nothing in the
app ever wrote a row to it or read one. A grid a user can type into that no
code reads is worse than no feature at all: sooner or later someone enters a
schedule there and believes the app is tracking it.

Building it properly needs a billing programme nobody has entered, so the
honest answer is to take it off the form rather than leave the promise
standing. Nothing is lost that was ever calculated; anything typed in by hand
is printed to the log first, so a site that did use it can see what it had.
"""

import frappe

DOCTYPE = "Project Cash Flow Item"


def execute():
    if not frappe.db.table_exists(DOCTYPE):
        frappe.delete_doc("DocType", DOCTYPE, ignore_missing=True, force=True)
        return

    rows = frappe.db.count(DOCTYPE)
    if rows:
        # Hand-entered, and about to go. Say what it was rather than drop it
        # silently — it is the only copy.
        frappe.log_error(
            frappe.as_json(frappe.db.sql(
                f"SELECT * FROM `tab{DOCTYPE}`", as_dict=True)),
            "Construction: Cash Flow Schedule rows removed",
        )

    frappe.delete_doc("DocType", DOCTYPE, ignore_missing=True, force=True)
    frappe.db.sql_ddl(f"DROP TABLE IF EXISTS `tab{DOCTYPE}`")
