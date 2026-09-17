"""Turning a certificate into an invoice.

Every generated invoice used to be a single line carrying an `item_name` and no
`item_code` — nothing to report on, nothing to tax, and the client could not see
what they were paying for. The work is now invoiced line by line against real
Items, and the deductions ride as charge rows rather than item lines.

Why deductions are charges, not negative item lines: ERPNext refuses to SUBMIT a
Sales Invoice containing a negative rate unless `Allow Negative rates for Items`
is switched on for the whole site. The accounting is also better this way — a
negative line would understate revenue, whereas a charge row credits Sales with
the full certified value and parks the withheld amount in its own account, which
is what retention actually is: money earned, owed to you, not yet due.
"""

import frappe
from frappe import _
from frappe.utils import flt

from construction_management_suite.utils.settings import cms_setting

# Non-stock service items the generated invoices are written against. Created on
# install; a site can point the settings at its own instead.
DEFAULT_ITEMS = {
    "progress_billing_item": ("SRV-PROGRESS-BILLING", "Progress Billing"),
    "subcontract_billing_item": ("SRV-SUBCONTRACT", "Subcontract Work"),
}


def billing_item(setting_name):
    """The Item configured for this kind of line, if it still exists."""
    item = cms_setting(setting_name)
    if item and frappe.db.exists("Item", item):
        return item
    fallback = DEFAULT_ITEMS.get(setting_name, (None, None))[0]
    return fallback if fallback and frappe.db.exists("Item", fallback) else None


def add_line(doc, item_code, description, amount, cost_center=None, qty=1, rate=None, uom=None):
    """One invoice line. Never negative — see the module docstring."""
    if not item_code or flt(amount) <= 0:
        return
    rate = flt(rate) if rate is not None else flt(amount)
    line = {
        "item_code": item_code,
        "description": description,
        "qty": flt(qty) or 1,
        "rate": rate,
        "cost_center": cost_center,
    }
    if uom:
        line["uom"] = uom
        line["conversion_factor"] = 1
    doc.append("items", line)


def add_deduction(doc, setting_name, description, amount, company, cost_center=None):
    """Withhold an amount on the face of the invoice, against its own account.

    Returns what it could not apply, so the caller can say so rather than
    silently issuing an invoice for more than the certificate allows.
    """
    amount = flt(amount)
    if not amount:
        return 0
    account = cms_setting(setting_name)
    if not account or not frappe.db.exists("Account", account):
        return amount
    if frappe.db.get_value("Account", account, "company") != company:
        return amount

    doc.append("taxes", {
        "charge_type": "Actual",
        "account_head": account,
        "description": description,
        # Negative: a deduction reduces what is due without touching revenue.
        "tax_amount": -amount,
        "cost_center": cost_center,
    })
    return 0


def apply_taxes(doc, setting_name):
    """Attach the configured tax template, if the site has named one."""
    template = cms_setting(setting_name)
    if not template:
        return
    field = doc.meta.get_field("taxes_and_charges")
    if not field or not frappe.db.exists(field.options, template):
        return
    doc.taxes_and_charges = template
    child = "Sales Taxes and Charges" if doc.doctype == "Sales Invoice" else "Purchase Taxes and Charges"
    for row in frappe.get_all(child, filters={"parent": template}, fields=["*"], order_by="idx"):
        for key in ("name", "parent", "parenttype", "parentfield", "creation", "modified", "owner", "modified_by", "idx"):
            row.pop(key, None)
        doc.append("taxes", row)


def warn_unapplied(doc, unapplied):
    """Say plainly when a deduction could not be posted."""
    missing = [label for label, amount in unapplied if flt(amount)]
    if not missing:
        return
    frappe.msgprint(
        _("{0} could not be deducted on {1} — no account is set for it in "
          "<b>Construction Settings</b>, so the invoice is for the full value.")
        .format(", ".join(missing), doc.doctype),
        title=_("Deduction not applied"),
        indicator="orange",
    )


def create_service_items():
    """Create the default billing items, once, on install."""
    group = "Services" if frappe.db.exists("Item Group", "Services") else "All Item Groups"
    uom = "Nos" if frappe.db.exists("UOM", "Nos") else frappe.db.get_value("UOM", {}, "name")
    created = []
    for setting_name, (code, name) in DEFAULT_ITEMS.items():
        if not frappe.db.exists("Item", code):
            frappe.get_doc({
                "doctype": "Item",
                "item_code": code,
                "item_name": name,
                "item_group": group,
                "stock_uom": uom,
                "is_stock_item": 0,
                "is_sales_item": 1,
                "is_purchase_item": 1,
                "description": _("Created by Construction Management Suite"),
            }).insert(ignore_permissions=True)
            created.append(code)
        if not cms_setting(setting_name):
            frappe.db.set_single_value("Construction Settings", setting_name, code)
    return created
