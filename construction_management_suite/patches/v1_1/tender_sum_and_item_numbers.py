"""Make the tender sum the sum of the priced lines, and number the lines.

`grand_total` used to be `total_amount` plus a percentage added below the line,
so a client adding up the printed Amount column reached a different figure from
the total on the same page — and the line explaining the gap was labelled
"Margin". Margin belongs inside the rates.

Only `grand_total` moves. No line rate, quantity or amount is touched.
"""

import frappe
from frappe.utils import flt


def execute():
    restate_grand_totals()
    number_existing_lines()


def restate_grand_totals():
    boqs = frappe.get_all(
        "BOQ", fields=["name", "project", "total_amount", "grand_total"]
    )
    for b in boqs:
        old, new = flt(b.grand_total), flt(b.total_amount)
        if abs(old - new) < 0.005:
            continue
        frappe.db.set_value("BOQ", b.name, "grand_total", new, update_modified=False)
        print(f"CMS: {b.name} grand total {old:,.2f} -> {new:,.2f}")
        carry_to_project(b.project, old, new)
        carry_to_certificates(b.name, old, new)


def carry_to_project(project, old, new):
    """Only where the project's contract value came from this BOQ.

    A contract value typed by hand, or moved by an approved Variation Order, is
    the agreed figure and outranks anything derived from the bill.
    """
    if not project:
        return
    current = flt(frappe.db.get_value("Project", project, "cms_contract_value"))
    if abs(current - old) > 0.005:
        return
    frappe.db.set_value("Project", project, "cms_contract_value", new, update_modified=False)
    print(f"CMS: project {project} contract value {current:,.2f} -> {new:,.2f}")


def carry_to_certificates(boq, old, new):
    """Certificates measure progress against the contract value they recorded.

    Nothing about what was certified or paid changes — `contract_value` is the
    denominator of the progress figure, not money.
    """
    for ipc in frappe.get_all(
        "Interim Payment Certificate", filters={"boq_ref": boq}, fields=["name", "contract_value"]
    ):
        if abs(flt(ipc.contract_value) - old) > 0.005:
            continue
        frappe.db.set_value(
            "Interim Payment Certificate", ipc.name, "contract_value", new, update_modified=False
        )
        print(f"CMS: {ipc.name} contract value {flt(ipc.contract_value):,.2f} -> {new:,.2f}")


def number_existing_lines():
    """Give lines already on file the reference scheme new ones get.

    Numbered in the order they are in today, so the numbers match the bills as
    they have already been printed and sent.
    """
    for boq in frappe.get_all("BOQ", pluck="name"):
        rows = frappe.get_all(
            "BOQ Item",
            filters={"parent": boq},
            fields=["name", "idx", "boq_section", "item_no"],
            order_by="idx",
        )
        if not rows or all(r.item_no for r in rows):
            continue

        sections = []
        for r in rows:
            key = r.boq_section or ""
            if key not in sections:
                sections.append(key)
        sectioned = len(sections) > 1 or (sections and sections[0])

        counters = {}
        for r in rows:
            if r.item_no:
                continue
            key = r.boq_section or ""
            counters[key] = counters.get(key, 0) + 1
            prefix = f"{sections.index(key) + 1}." if sectioned else ""
            frappe.db.set_value(
                "BOQ Item", r.name, "item_no", f"{prefix}{counters[key]}", update_modified=False
            )
