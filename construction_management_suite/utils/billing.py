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
# The items the generated invoices are written against. The description is what
# the client and the subcontractor read on the invoice, so it says what is being
# paid for in the trade's own terms — not that an app created it.
DEFAULT_ITEMS = {
    "progress_billing_item": (
        "SRV-PROGRESS-BILLING",
        "Progress Billing",
        "Work certified this period, net of retention and advance recovery.",
    ),
    # Its own item, not progress billing: releasing retention is returning money
    # already earned and withheld, not billing new work, and an income report
    # that cannot tell the two apart overstates the period it lands in.
    "retention_release_item": (
        "SRV-RETENTION-RELEASE",
        "Retention Release",
        "Retention held on earlier certificates, released under the contract.",
    ),
    "subcontract_billing_item": (
        "SRV-SUBCONTRACT",
        "Subcontract Work",
        "Work certified under a subcontract, net of retention and advance recovery.",
    ),
}


def orderable_qty(qty, uom):
    """Round a take-off quantity up to something you can actually buy.

    A take-off with a waste factor produces 8,059.8 bags of cement, and ERPNext
    refuses a fraction on a UOM marked Must Be Whole Number. Rounding UP, never
    down: ordering 8,059 leaves the job short by design.
    """
    import math

    qty = flt(qty)
    if not uom or qty <= 0:
        return qty
    if frappe.db.get_value("UOM", uom, "must_be_whole_number"):
        return float(math.ceil(qty - 0.000001))
    return qty


def money(doc, value):
    """Format a figure in the document's own currency, for a message."""
    return frappe.format_value(
        flt(value), {"fieldtype": "Currency", "options": "currency"}, doc
    )


def billing_item(setting_name):
    """The Item configured for this kind of line, if it still exists."""
    item = cms_setting(setting_name)
    if item and frappe.db.exists("Item", item):
        return item
    fallback = DEFAULT_ITEMS.get(setting_name, (None,))[0]
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
    """An account named in Construction Settings, checked against the company.

    One global value, because the setting is one field. An Account belongs to a
    Company in ERPNext, so on a site running a second company the caller is told
    the setting is unusable rather than posting to the wrong books — see
    add_deduction.

    Tax templates are NOT read from here any more: ERPNext already keeps a
    default per company on the template itself, which is the answer on a
    multi-company site and the one users expect from every other document. See
    `default_tax_template`.
    """
    value = cms_setting(fieldname)
    if not value or not company:
        return value or None
    owner = frappe.db.get_value("Account", value, "company")
    return value if not owner or owner == company else None


def default_tax_template(doc):
    """The company's own default tax template — ERPNext's, not ours.

    The module used to name one tax template per site in its settings, which is
    wrong the moment a second company exists: the template carries the accounts,
    and the accounts belong to a company. ERPNext already answers this with
    `is_default` on the template, per company, and every standard document reads
    it that way. So do these.

    A company with no default marked simply gets no template, and the user picks
    one — the same as a Sales Invoice.
    """
    from erpnext.controllers.accounts_controller import get_default_taxes_and_charges

    field = doc.meta.get_field("taxes_and_charges")
    if not field or not doc.get("company"):
        return None
    defaults = get_default_taxes_and_charges(
        field.options, doc.get("taxes_and_charges"), doc.company
    ) or {}
    return defaults.get("taxes_and_charges")


# Descriptions this app has written before now. Replaced on upgrade, because
# they go out on invoices — the first said only that an app had created the
# item, the second said the right thing at three times the length. A
# description somebody else has written is left alone.
_SUPERSEDED = (
    "Created by Construction Management Suite",
    "Value of work executed during the period, measured and certified against "
    "the contract bill of quantities and any approved variations. Billed net of "
    "retention and of any advance recovered in the period.",
    "Release of retention withheld from earlier payment certificates, due on "
    "practical completion or on expiry of the defects liability period under "
    "the contract.",
    "Subcontracted work certified as executed under a subcontract agreement, "
    "valued at the agreed rates and net of subcontract retention and of any "
    "advance recovered.",
)


def create_service_items():
    """Create the default billing items, once, on install."""
    group = "Services" if frappe.db.exists("Item Group", "Services") else "All Item Groups"
    uom = "Nos" if frappe.db.exists("UOM", "Nos") else frappe.db.get_value("UOM", {}, "name")
    created = []
    for setting_name, (code, name, description) in DEFAULT_ITEMS.items():
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
                "description": _(description),
            }).insert(ignore_permissions=True)
            created.append(code)
        elif _is_ours(frappe.db.get_value("Item", code, "description")):
            frappe.db.set_value("Item", code, "description", _(description))
        if not cms_setting(setting_name):
            frappe.db.set_single_value("Construction Settings", setting_name, code)
    return created


def _is_ours(description):
    """Is this a description this app wrote, rather than one a user did?"""
    text = (description or "").replace("<div>", "").replace("</div>", "").strip()
    return not text or text in _SUPERSEDED


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


def refuse_empty(target, source):
    """Never hand ERPNext an invoice with no lines.

    add_line skips a non-positive amount, so a nil document produced an invoice
    with an empty items table — and ERPNext dies computing a payment schedule
    for it, with a TypeError that names none of the documents involved. Say
    which document is empty and why.
    """
    if target.get("items"):
        return False
    frappe.throw(
        _("{0} {1} has nothing to invoice — every line came to zero.").format(
            _(source.doctype), source.name
        ),
        title=_("Nothing to invoice"),
    )


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


def validate_tax_template_company(doc):
    """A tax template belongs to a company, and so does the account under it.

    The module default is already checked against the document's company by
    `company_setting`, but a template picked by hand was not checked at all —
    so an agreement for one company could carry another company's VAT, and the
    order or invoice it raises would post to books it has no business in.
    ERPNext refuses it at the far end, on a document the user did not write.
    """
    template = doc.get("taxes_and_charges")
    if not template or not doc.get("company"):
        return
    field = doc.meta.get_field("taxes_and_charges")
    if not field:
        return
    owner = frappe.db.get_value(field.options, template, "company")
    if not owner or owner == doc.company:
        return
    frappe.throw(
        _(
            "{0} belongs to {1}, but this document is for {2}.<br><br>Its tax "
            "accounts are {1}'s, so the invoice or order raised from here would "
            "post to the wrong company's books."
        ).format(template, owner, doc.company),
        title=_("Tax template belongs to another company"),
    )


def stamp_accounting(target, project=None, cost_center=None):
    """Put the job's accounting dimensions on a document this app raises.

    Every document here is built field by field and inserted, which skips the
    form entirely — so anything the form would have fetched has to be said out
    loud. The cost centre was being written onto the rows and left blank on the
    document, where ERPNext shows it and where a user looks for it; the project
    was on some rows and not others, and a Sales Invoice line with no project
    is invisible to every project-wise report.

    Only fills a blank, and only where the field exists: a Material Request has
    no cost centre of its own and must not be given one.
    """
    for fieldname, value in (("cost_center", cost_center), ("project", project)):
        if not value:
            continue
        if target.meta.get_field(fieldname) and not target.get(fieldname):
            target.set(fieldname, value)
        for row in target.get("items") or []:
            if row.meta.get_field(fieldname) and not row.get(fieldname):
                row.set(fieldname, value)


def carry_taxes(source, target, cost_center=None):
    """Copy a document's tax rows onto the document it raises.

    The source is what was agreed and taxed — an agreement, a certificate — so
    the order or invoice it raises cannot say something different.

    A **purchase** tax row carries two more mandatory fields than a sales one,
    `category` and `add_deduct_tax`, and they were not copied. It went unnoticed
    for as long as every target was inserted on the server, because `insert()`
    fills a missing default; a document mapped and handed to the browser
    unsaved never goes through insert, so its rows arrived without them and the
    form refused to save with two mandatory-field errors nobody could act on.
    """
    target.taxes_and_charges = source.taxes_and_charges
    child = target.meta.get_field("taxes")
    child_meta = frappe.get_meta(child.options) if child else None

    for row in source.get("taxes") or []:
        line = {
            "charge_type": row.charge_type,
            "account_head": row.account_head,
            "description": row.description,
            "rate": row.rate,
            "tax_amount": row.tax_amount,
            "row_id": row.row_id,
            "cost_center": row.cost_center or cost_center,
            "included_in_print_rate": row.included_in_print_rate,
        }
        # ERPNext's own defaults as the fallback, so a row copied from a source
        # that predates this is still complete.
        for fieldname, fallback in (("category", "Total"), ("add_deduct_tax", "Add")):
            if child_meta and child_meta.get_field(fieldname):
                line[fieldname] = row.get(fieldname) or fallback
        target.append("taxes", line)


def check_advance_recovery(doc, advance, recovered, billed, contract, label):
    """Flag an advance still outstanding when the job is nearly billed out.

    An advance is money handed over before any work was done, clawed back a
    slice at a time from each certificate — by hand, because how much to recover
    this period is a commercial decision. Nothing checked it ever reached zero,
    so a job could finish with the advance simply given away. Chased only once
    billing passes a threshold, because early on an outstanding advance is
    exactly what it should be.
    """
    from construction_management_suite.utils.settings import action_for, cms_setting

    action = action_for("advance_recovery_action")
    outstanding = flt(advance) - flt(recovered)
    if action == "Ignore" or outstanding <= 0.005 or not flt(contract):
        return
    threshold = flt(cms_setting("advance_recovery_threshold_percent", 0))
    progress = flt(billed) / flt(contract) * 100
    if progress < threshold:
        return

    from construction_management_suite.utils.settings import enforce

    enforce(
        action,
        _("{0} of the {1} advance is still outstanding, and {2}% of the contract "
          "has been billed. Recover it on this certificate or the remaining ones.")
        .format(
            frappe.format_value(outstanding, {"fieldtype": "Currency"}, doc),
            label,
            flt(progress, 1),
        ),
        title=_("Advance not recovered"),
    )
