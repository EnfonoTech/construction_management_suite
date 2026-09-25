"""What the module says before a project buys something.

Hooked onto ERPNext's Purchase Order rather than written into a controller,
because the doctype is not ours. Both checks read their severity from
Construction Settings, so a site decides whether they inform or block.

The checks only speak — nothing here posts or changes a figure. The one thing
it writes is a blank warehouse, filled from the project's own store, and only
where the buyer left it blank.
"""

import frappe
from frappe import _
from frappe.utils import flt

from construction_management_suite.utils.accounting import get_warehouse
from construction_management_suite.utils.settings import action_for, cms_setting, enforce


def validate_purchase_document(doc, method=None):
    """Everything this module says before a project's money is spent.

    Hooked on the order, the receipt and the invoice alike. It used to run on
    the Purchase Order only, so buying straight through a Purchase Invoice with
    Update Stock — which is a purchase and a receipt in one document, and a
    normal way to buy on site — was checked against nothing at all: not the
    budget, not the estimated rate, not the plan.
    """
    check_against_the_plan(doc)
    check_against_project_budget(doc)
    check_rates_against_estimate(doc)


# Kept: the hook name that shipped.
validate_purchase_order = validate_purchase_document


def check_against_the_plan(doc):
    """Refuse material bought for work this project is not doing.

    Two different mistakes, and they deserve different answers.

    A line naming a **work item that is not on the project's estimate** is a
    reference to nothing: the project never priced that work, so every figure
    reported per work item — Material Position, the take-off, the consumption
    check — silently excludes it. That is stopped.

    A line naming a **material the plan does not include for that work** is not
    necessarily wrong. Jobs buy things nobody foresaw. But it is the moment the
    job departs from its estimate, and it should be said out loud rather than
    discovered in the variance three months later.
    """
    from construction_management_suite.material_planning.doctype.material_consumption_entry.material_consumption_entry import (
        take_off_by_line,
    )
    from construction_management_suite.utils.validations import current_estimate

    foreign_action = action_for("foreign_work_item_action", "Stop")
    unplanned_action = action_for("unplanned_material_action")
    if foreign_action == "Ignore" and unplanned_action == "Ignore":
        return

    # A purchase invoice that moves no stock is buying a service or an expense,
    # not material against a plan.
    if doc.doctype == "Purchase Invoice" and not doc.get("update_stock"):
        return

    plans, foreign, unplanned = {}, [], []
    for row in doc.get("items") or []:
        project = row.get("project") or doc.get("project")
        item = row.get("item_code")
        if not project or not item:
            continue
        # A subcontract order buys work, not material; the work item IS the line.
        if not frappe.db.get_value("Item", item, "is_stock_item"):
            continue
        if project not in plans:
            plans[project] = _plan_for(project, current_estimate, take_off_by_line)
        plan = plans[project]
        if plan is None:
            # No estimate at all — a different complaint, and one the module
            # already makes where it matters. Nothing to compare against here.
            continue
        works, take_off = plan
        work = row.get("cms_work_item")
        if work and work not in works:
            foreign.append((row, work, project))
            continue
        if (item, work or None) in take_off:
            continue
        # The take-off is keyed by material AND work. A line that has not said
        # which work it is for matches no key, and calling that "not in the
        # plan" was wrong — the material may be planned under every work there
        # is. Judge an unattributed line on the material alone.
        if not work and any(code == item for code, _w in take_off):
            continue
        unplanned.append((row, work, project))

    if foreign and foreign_action != "Ignore":
        enforce(
            foreign_action,
            "<br>".join(
                _("Row {0}: {1} is being bought for {2}, which is not work on {3}.").format(
                    r.idx, r.item_code, work, project
                )
                for r, work, project in foreign[:10]
            )
            + _("<br><br>Pick a line of work from that project's Cost Estimation, or "
                "clear the reference — as it stands the purchase is attributed to "
                "work the project is not doing, and every per-work figure ignores it."),
            title=_("{0} line(s) for work not on this project").format(len(foreign)),
        )

    if unplanned and unplanned_action != "Ignore":
        enforce(
            unplanned_action,
            "<br>".join(
                _("Row {0}: {1} is not in {2}'s plan{3}.").format(
                    r.idx, r.item_code, project,
                    _(" for {0}").format(work) if work else "",
                )
                for r, work, project in unplanned[:10]
            )
            + _("<br><br>The Cost Estimation does not price this material against that "
                "work, so it is a departure from the plan: nothing nets it off a "
                "take-off and it lands in the variance."),
            title=_("{0} line(s) the plan does not include").format(len(unplanned)),
        )


def _plan_for(project, current_estimate, take_off_by_line):
    """What a project is priced to buy: its work items, and its take-off."""
    estimate = current_estimate(project)
    if not estimate:
        return None
    works = set(
        frappe.get_all("Cost Estimation Item", filters={"parent": estimate}, pluck="item_code")
    )
    return works, take_off_by_line(project)


def set_project_warehouse(doc, method=None):
    """Deliver to the job's own store when the line does not say otherwise.

    ERPNext fills the warehouse from the buyer's own defaults, which on a
    contractor's site is whichever job they last bought for. The line already
    says which project it is for; where that project keeps one store, that is
    the answer, and a project keeping several deliberately names none.

    Hooked on `before_validate`, not `validate`: ERPNext's own validate refuses
    a stock line with no warehouse, and a doc_event runs after the controller
    it is attached to — so by `validate` the throw has already happened.

    A purchase invoice only moves stock when it says it does; filling a
    warehouse on one that does not would be answering a question nobody asked.
    """
    if doc.doctype == "Purchase Invoice" and not doc.get("update_stock"):
        return

    stores = {}
    for row in doc.get("items") or []:
        if row.get("warehouse"):
            continue
        project = row.get("project") or doc.get("project")
        if not project:
            continue
        if project not in stores:
            stores[project] = get_warehouse(project, doc.company)
        if stores[project]:
            row.warehouse = stores[project]


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
    """What this document spends per project. The project is on the line.

    A receipt or an invoice that came from an order is spending money the order
    already committed, so those lines are left out — counting both would read
    as double the spend and cry over-budget on every second document.
    """
    totals = {}
    for row in doc.get("items") or []:
        if not row.get("project"):
            continue
        if row.get("purchase_order") or row.get("po_detail"):
            continue
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


def close_subcontract_order(doc, method=None):
    """Finish a subcontract order once its last invoice is in.

    ERPNext closes a purchase order when it has been both received and billed.
    A subcontract is a service: nothing can receive it, `per_received` stays at
    zero and the order sits at "To Receive" however much has been billed — so
    every subcontract order on the site stayed open for ever. Closing it is
    ERPNext's own answer to that, and being billed in full is when it is true.

    Only orders this module raised, and only ever to close: reopening one is a
    decision, and ERPNext has a button for it.
    """
    orders = {row.purchase_order for row in doc.get("items") or [] if row.get("purchase_order")}
    for name in orders:
        order = frappe.db.get_value(
            "Purchase Order", name,
            ["docstatus", "status", "per_billed", "cms_subcontract_ref"],
            as_dict=True,
        )
        if not order or not order.cms_subcontract_ref:
            continue
        if order.docstatus != 1 or order.status in ("Closed", "Cancelled"):
            continue
        if flt(order.per_billed) < 99.995:
            continue
        frappe.get_doc("Purchase Order", name).update_status("Closed")
        frappe.msgprint(
            _("Purchase Order {0} closed — billed in full, and a subcontract has "
              "nothing left to receive.").format(name),
            alert=True,
        )
