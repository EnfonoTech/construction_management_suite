"""Put every submitted document through cancel, amend and submit again.

Importing a module proves its imports; it says nothing about a name used only
inside a method that runs on submit. `project_label` was missing from Retention
Release for exactly that reason and only showed up when a release was actually
submitted.

    cd sites && ../env/bin/python <app>/tests/lifecycle.py

Everything is rolled back.
"""

import sys
import traceback

import frappe

PROJECT = "PROJ-0009"
DOCTYPES = [
    "BOQ", "Cost Estimation", "Variation Order", "Interim Payment Certificate",
    "Retention Release", "Subcontract Agreement", "Subcontractor Work Order",
    "Subcontractor Payment Certificate", "Material Consumption Entry",
    "Daily Site Report", "Project Budget", "Material Forecast",
]


def run():
    frappe.init(site="misk")
    frappe.connect()
    frappe.set_user("Administrator")
    failures = []

    for doctype in DOCTYPES:
        names = frappe.get_all(
            doctype, filters={"project": PROJECT, "docstatus": 1}, pluck="name", limit=1
        ) if frappe.get_meta(doctype).get_field("project") else []
        if not names:
            print(f"  skip   {doctype:<36} nothing submitted on {PROJECT}")
            continue
        try:
            _cycle(doctype, names[0])
            print(f"  OK     {doctype:<36} {names[0]}")
        except frappe.LinkExistsError:
            # Correct: a bill with live certificates against it, or an
            # agreement with a certificate, must not be cancellable.
            print(f"  GUARD  {doctype:<36} {names[0]}  protected by its dependants")
            frappe.db.rollback()
        except Exception as e:
            failures.append((doctype, names[0], f"{type(e).__name__}: {e}"))
            print(f"  FAIL   {doctype:<36} {names[0]}  {type(e).__name__}: {str(e)[:90]}")
            if "--trace" in sys.argv:
                traceback.print_exc()
            frappe.db.rollback()

    print(f"\n{len(DOCTYPES)} doctypes, {len(failures)} failure(s)")
    frappe.db.rollback()
    frappe.destroy()
    return failures


def _cycle(doctype, name):
    doc = frappe.get_doc(doctype, name)
    doc.cancel()
    doc.reload()
    assert doc.docstatus == 2, "did not cancel"

    # ignore_no_copy=False is what the desk's amend button does; the default
    # True would carry across the very fields marked no_copy.
    amended = frappe.copy_doc(doc, ignore_no_copy=False)
    amended.amended_from = doc.name
    amended.insert()
    assert amended.docstatus == 0, "amendment did not open as a draft"
    if amended.meta.get_field("status"):
        assert amended.status != "Cancelled", f"amendment opened as {amended.status}"
    amended.submit()
    assert amended.docstatus == 1, "amendment did not submit"


if __name__ == "__main__":
    sys.exit(1 if run() else 0)
