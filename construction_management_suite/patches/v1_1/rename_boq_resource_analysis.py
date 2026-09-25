"""The report reads the Cost Estimation by default, so its name no longer fits.

`BOQ Resource Analysis` was named when a BOQ was the only thing to explode. It
now defaults to the Cost Estimation and works on a project that has no BOQ at
all, which is the ordinary case — so the name told a new user the report was
not for them.

Renaming the folder gives migrate a new standard Report and leaves the old
record behind, still listed and still pointing at a Python module that no
longer exists. `rename_doc` is not used: the report is standard, so the new
record is created from the file anyway, and renaming would only fight it.
Anything a user saved against the old name — a Dashboard Chart, an Auto Email
Report — is repointed first, then the orphan is dropped.
"""

import frappe

OLD = "BOQ Resource Analysis"
NEW = "Resource Take-off"


def execute():
    if not frappe.db.exists("Report", OLD):
        return

    for doctype, field in (
        ("Dashboard Chart", "report_name"),
        ("Auto Email Report", "report"),
        ("Workspace Link", "link_to"),
    ):
        if not frappe.db.exists("DocType", doctype):
            continue
        frappe.db.sql(
            "UPDATE `tab{dt}` SET `{field}` = %s WHERE `{field}` = %s".format(dt=doctype, field=field),
            (NEW, OLD),
        )

    frappe.delete_doc("Report", OLD, ignore_permissions=True, force=True, ignore_missing=True)
