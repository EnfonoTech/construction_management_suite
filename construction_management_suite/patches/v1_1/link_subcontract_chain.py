"""Attach existing work orders and certificates to the lines they deliver.

`agreement_item_ref` and `work_order_item_ref` are new, so every document
written before them is unattributable — and an order whose quantity cannot be
counted makes its agreement look wholly uninstructed, which is how the same work
gets instructed twice.

Matched in descending order of confidence and never guessed: a row that stays
ambiguous is reported for a human to link, because attaching a payment to the
wrong line of a contract is worse than leaving it unattached.
"""

import frappe
from frappe.utils import flt


def execute():
    unresolved = []
    unresolved += link_work_orders()
    unresolved += link_certificates()
    if unresolved:
        print("Construction: link these by hand — " + "; ".join(unresolved))


def link_work_orders():
    unresolved = []
    rows = frappe.db.sql(
        """
        SELECT i.name, i.description, i.item_code, i.contract_rate, w.subcontract_agreement AS agreement
        FROM `tabSubcontractor Work Order Item` i
        JOIN `tabSubcontractor Work Order` w ON w.name = i.parent
        WHERE (i.agreement_item_ref IS NULL OR i.agreement_item_ref = '')
          AND w.subcontract_agreement IS NOT NULL
        """,
        as_dict=True,
    )
    for row in rows:
        candidates = frappe.get_all(
            "Subcontract Item", filters={"parent": row.agreement},
            fields=["name", "description", "item_code", "rate", "boq_ref", "boq_item_no"],
        )
        match = _best(row, candidates, "rate")
        if not match:
            unresolved.append(f"work order line {row.name}")
            continue
        frappe.db.set_value("Subcontractor Work Order Item", row.name, {
            "agreement_item_ref": match.name,
            "item_code": row.item_code or match.item_code,
            "boq_ref": match.boq_ref,
            "boq_item_no": match.boq_item_no,
        }, update_modified=False)
    return unresolved


def link_certificates():
    unresolved = []
    rows = frappe.db.sql(
        """
        SELECT i.name, i.description, i.item_code, i.contract_rate, c.subcontract_agreement AS agreement
        FROM `tabSubcontractor Payment Item` i
        JOIN `tabSubcontractor Payment Certificate` c ON c.name = i.parent
        WHERE (i.work_order_item_ref IS NULL OR i.work_order_item_ref = '')
          AND c.subcontract_agreement IS NOT NULL
        """,
        as_dict=True,
    )
    for row in rows:
        candidates = frappe.db.sql(
            """
            SELECT i.name, i.description, i.item_code, i.contract_rate AS rate,
                   i.boq_ref, i.boq_item_no
            FROM `tabSubcontractor Work Order Item` i
            JOIN `tabSubcontractor Work Order` w ON w.name = i.parent
            WHERE w.subcontract_agreement = %s AND w.docstatus < 2
            """,
            row.agreement, as_dict=True,
        )
        match = _best(row, candidates, "rate")
        if not match:
            unresolved.append(f"certificate line {row.name}")
            continue
        frappe.db.set_value("Subcontractor Payment Item", row.name, {
            "work_order_item_ref": match.name,
            "item_code": row.item_code or match.item_code,
            "boq_ref": match.boq_ref,
            "boq_item_no": match.boq_item_no,
        }, update_modified=False)
    return unresolved


def _best(row, candidates, rate_field):
    """Exact description, then unique item, then unique rate. Never a guess."""
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    exact = [c for c in candidates if (c.description or "") == (row.description or "")]
    if len(exact) == 1:
        return exact[0]

    if row.item_code:
        by_item = [c for c in candidates if c.item_code == row.item_code]
        if len(by_item) == 1:
            return by_item[0]

    if flt(row.contract_rate):
        by_rate = [c for c in candidates
                   if abs(flt(c.get(rate_field)) - flt(row.contract_rate)) < 0.005]
        if len(by_rate) == 1:
            return by_rate[0]
    return None
