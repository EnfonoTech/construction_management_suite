"""Turning a certificate into an invoice.

Every generated invoice used to be a single line carrying an `item_name` and no
`item_code` — nothing to report on, nothing to tax, and the client could not see
what they were paying for. The work is now invoiced against a real Item named in
Construction Settings, and the deductions ride as charge rows rather than item
lines.

Retention and the recoveries are not lines on the invoice at all: they have
already come off by the time it is raised, so the invoice is for what is due and
the certificate carries the breakdown showing how it got there. Never as negative
item lines — ERPNext refuses to SUBMIT a Sales Invoice with a negative rate
unless `Allow Negative rates for Items` is on for the whole site.
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


def company_setting(company, fieldname):
    """An account or tax template from Construction Settings, checked against
    the document's company.

    One global value, because this site runs a single construction company. An
    Account and a tax template both belong to a Company in ERPNext, so if a
    second one is ever added the callers report the setting as unusable rather
    than posting to the wrong books — see add_deduction and apply_taxes.
    """
    value = cms_setting(fieldname)
    if not value or not company:
        return value or None
    doctype = "Account" if fieldname.endswith("_account") else (
        "Sales Taxes and Charges Template" if fieldname.startswith("sales")
        else "Purchase Taxes and Charges Template")
    owner = frappe.db.get_value(doctype, value, "company")
    return value if not owner or owner == company else None


def apply_taxes(doc, setting_name, company=None):
    """Attach the configured tax template, if the company has named one."""
    template = company_setting(company or doc.get("company"), setting_name)
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


def load_tax_template(doc):
    """Fill an empty taxes table from the template named on the document.

    Setting `taxes_and_charges` alone did nothing — only a manual change in the
    form loaded the rows, so a certificate saved straight from the API, an
    import, or the Next Certificate button carried a template and no tax.
    """
    if not doc.get("taxes_and_charges") or doc.get("taxes"):
        return
    from erpnext.controllers.accounts_controller import get_taxes_and_charges

    master = doc.meta.get_field("taxes_and_charges").options
    if not frappe.db.exists(master, doc.taxes_and_charges):
        return
    for row in get_taxes_and_charges(master, doc.taxes_and_charges) or []:
        doc.append("taxes", row)


def calculate_taxes(doc, base_amount, net_field="total_payable"):
    """Apply a taxes table to a base amount, ERPNext's way.

    Follows `calculate_taxes` in erpnext's taxes_and_totals so a certificate and
    the document it raises reach the same figure. On Item Quantity throws rather
    than guessing: it needs per-item tax handling these documents do not have,
    and a figure nobody can explain is worse than a refusal.
    """
    base_amount = flt(base_amount)
    running = base_amount
    for row in doc.get("taxes") or []:
        if row.charge_type == "Actual":
            amount = flt(row.tax_amount)
        elif row.charge_type == "On Net Total":
            amount = base_amount * flt(row.rate) / 100
        elif row.charge_type in ("On Previous Row Amount", "On Previous Row Total"):
            field = "tax_amount" if row.charge_type == "On Previous Row Amount" else "total"
            amount = flt(_previous_row(doc, row, field)) * flt(row.rate) / 100
        else:
            frappe.throw(
                _("Row {0}: charge type {1} is not supported on a {2}").format(
                    row.idx, row.charge_type, _(doc.doctype)
                )
            )
        row.tax_amount = amount
        running += amount
        row.total = running

    doc.total_taxes_and_charges = sum(flt(r.tax_amount) for r in doc.get("taxes") or [])
    return flt(doc.total_taxes_and_charges)


def _previous_row(doc, row, fieldname):
    if not row.row_id:
        frappe.throw(_("Row {0}: set the row it is charged on").format(row.idx))
    idx = int(row.row_id)
    if idx >= row.idx:
        frappe.throw(_("Row {0} can only refer to a row above it").format(row.idx))
    return doc.taxes[idx - 1].get(fieldname)


def carry_taxes(source, target, cost_center=None):
    """Copy a document's tax rows onto the ERPNext document it raises.

    The certificate is the source: whatever was agreed and taxed there is what
    gets invoiced, so the two cannot say different things.
    """
    target.taxes_and_charges = source.taxes_and_charges
    for row in source.get("taxes") or []:
        target.append("taxes", {
            "charge_type": row.charge_type,
            "account_head": row.account_head,
            "description": row.description,
            "rate": row.rate,
            "tax_amount": row.tax_amount,
            "row_id": row.row_id,
            "cost_center": row.cost_center or cost_center,
            "included_in_print_rate": row.included_in_print_rate,
        })
