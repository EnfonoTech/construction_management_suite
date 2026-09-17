"""What the module says before a project buys something.

Hooked onto ERPNext's Purchase Order rather than written into a controller,
because the doctype is not ours. Both checks read their severity from
Construction Settings, so a site decides whether they inform or block.

Nothing here posts, writes or changes a figure — the checks only speak.
"""

import frappe
from frappe import _
from frappe.utils import flt

from construction_management_suite.utils.settings import action_for, cms_setting, enforce


def validate_purchase_order(doc, method=None):
    check_against_project_budget(doc)
    check_rates_against_estimate(doc)


def check_against_project_budget(doc):
    """Flag an order that would take a project past what was budgeted for it.

    ERPNext's own Budget doctype enforces against a Cost Center or Project and
    should be used where the accounting has to hold the line. This is the
    softer, project-manager-facing version: it reads the Project Budget this
    module already maintains, which is seeded from the Cost Estimation.
    """
    action = action_for("po_over_budget_action")
    if action == "Ignore":
        return

    for project, ordered in _amount_by_project(doc).items():
        budget = frappe.db.get_value(
            "Project Budget",
            {"project": project, "docstatus": ("<", 2)},
            ["name", "total_budget", "total_actual_cost", "total_committed_cost"],
            as_dict=True,
        )
        if not budget or not flt(budget.total_budget):
            continue
        committed = flt(budget.total_actual_cost) + flt(budget.total_committed_cost)
        after = committed + flt(ordered)
        if after <= flt(budget.total_budget):
            continue
        enforce(
            action,
            _(
                "{0} is budgeted at {1}. {2} is already spent or committed, and this "
                "order adds {3} — taking it to {4}, over by {5}."
            ).format(
                project,
                _fmt(budget.total_budget, doc),
                _fmt(committed, doc),
                _fmt(ordered, doc),
                _fmt(after, doc),
                _fmt(after - flt(budget.total_budget), doc),
            ),
            title=_("Over the project budget"),
        )


def check_rates_against_estimate(doc):
    """Flag buying a material above the rate the work was priced at.

    The estimated rate is whatever the item was costed at in the rate library —
    the same figure the BOQ take-off was built from. Buying above it is not
    wrong, but it is the moment a job starts losing money, and it is invisible
    otherwise.
    """
    action = action_for("purchase_rate_action")
    if action == "Ignore":
        return
    tolerance = flt(cms_setting("purchase_rate_tolerance_percent", 0))

    over = []
    for row in doc.get("items") or []:
        if not row.get("project") or not row.get("item_code"):
            continue
        estimated = _estimated_rate(row.item_code)
        if not estimated:
            continue
        ceiling = estimated * (1 + tolerance / 100)
        if flt(row.rate) <= ceiling:
            continue
        over.append((row, estimated, ceiling))

    if not over:
        return
    enforce(
        action,
        "<br>".join(
            _("Row {0} ({1}): buying at {2}, estimated at no more than {3}{4}").format(
                r.idx,
                r.item_code,
                _fmt(r.rate, doc),
                _fmt(est, doc),
                _(" (tolerance {0})").format(_fmt(ceiling, doc)) if tolerance else "",
            )
            for r, est, ceiling in over[:10]
        ),
        title=_("{0} line(s) above the estimated rate").format(len(over)),
    )


def _amount_by_project(doc):
    """Purchase Orders carry the project per line, not on the header."""
    totals = {}
    for row in doc.get("items") or []:
        if row.get("project"):
            totals[row.project] = totals.get(row.project, 0) + flt(row.get("base_amount") or row.get("amount"))
    return totals


def _estimated_rate(item_code):
    """The highest rate this item is costed at across the live rate library.

    One material legitimately appears in several analyses at different rates —
    different specifications, suppliers, or a rate built for a different unit.
    On this site cement sits at 2.5 in one approved analysis and 20.0 in
    another. Picking one of them arbitrarily would cry wolf on every purchase
    above the cheaper figure, and a control that is usually wrong gets switched
    off. The maximum only fires when the purchase is above every estimate, which
    is the case nobody can argue with.
    """
    rate = frappe.db.sql(
        """
        SELECT MAX(r.rate)
        FROM `tabRate Analysis Resource` r
        JOIN `tabRate Analysis` ra ON ra.name = r.parent
        WHERE r.resource_item = %s AND ra.is_active = 1 AND ra.status = 'Approved'
        """,
        item_code,
    )
    return flt(rate[0][0]) if rate and rate[0][0] else 0


def _fmt(value, doc):
    return frappe.format_value(
        flt(value), {"fieldtype": "Currency", "options": "currency"}, doc
    )
