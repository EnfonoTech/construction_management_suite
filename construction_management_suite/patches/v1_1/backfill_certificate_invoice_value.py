import frappe
from frappe.utils import flt


def execute():
    """Fill the two figures added when the invoice stopped being the net payable.

    A certificate now states what the supplier invoices — the certified value
    plus its tax — beside what is paid this month, which is that less what is
    withheld. Both are computed as a certificate is saved, and a submitted one
    is never saved again, so every certificate raised before this read zero on
    the form and printed zero on the document.
    """
    meta = frappe.get_meta("Subcontractor Payment Certificate")
    if not meta.get_field("invoice_amount"):
        return

    for row in frappe.get_all(
        "Subcontractor Payment Certificate",
        filters={"docstatus": ("<", 2)},
        fields=["name", "certified_amount", "total_taxes_and_charges",
                "retention_deduction", "advance_recovery", "other_deductions"],
    ):
        frappe.db.set_value(
            "Subcontractor Payment Certificate",
            row.name,
            {
                "invoice_amount": flt(row.certified_amount) + flt(row.total_taxes_and_charges),
                "total_withheld": (
                    flt(row.retention_deduction)
                    + flt(row.advance_recovery)
                    + flt(row.other_deductions)
                ),
            },
            update_modified=False,
        )
