import frappe
from frappe import _
from frappe.utils import flt


def validate_project_company(doc, project_fields=("project",)):
    """A document must belong to the same company as the project it is against.

    On a site running several companies, attaching a certificate or a budget to
    another company's project silently posts to the wrong books, so this is
    enforced on the server rather than left to the form's link filter.
    """
    if not doc.get("company"):
        return
    for fieldname in project_fields:
        project = doc.get(fieldname)
        if not project:
            continue
        project_company = frappe.db.get_value("Project", project, "company")
        if project_company and project_company != doc.company:
            frappe.throw(
                _("{0} {1} belongs to company {2}, but this document is for {3}").format(
                    _(doc.meta.get_label(fieldname)), project, project_company, doc.company
                ),
                title=_("Company Mismatch"),
            )


# ── The model: work is a service Item, material is a stock Item ──────────────
#
# A "work item" is a line of work — CMS-RCC-M30, CMS-BLK-200 — and is held as an
# Item with `is_stock_item = 0`. A material is something a store issues and has
# `is_stock_item = 1`. A Rate Analysis is the bill of materials joining the two.
#
# Nothing enforced this, and the data drifted: a stock item was being priced as a
# line of work, and an approved analysis carried material rows naming no Item at
# all — invisible to the take-off, the forecast, Material Position and the
# consumption check alike, with nothing on screen to say so.


def is_work_item(item_code):
    """A service Item — something the job builds, not something a store issues."""
    if not item_code:
        return None
    return not frappe.db.get_value("Item", item_code, "is_stock_item")


def validate_work_item(item_code, row_idx=None):
    """Flag a line of work being priced against a stock Item.

    Warn by default rather than Stop: a site part-way through setting its Item
    master up should be told, not blocked.
    """
    from construction_management_suite.utils.settings import action_for, enforce

    action = action_for("work_item_type_action")
    if action == "Ignore" or not item_code:
        return
    if is_work_item(item_code):
        return
    where = _("Row {0}: ").format(row_idx) if row_idx else ""
    enforce(
        action,
        _(
            "{0}{1} is a stock item, but it is being used as a line of work.<br><br>"
            "A line of work is a <b>service</b> item (Maintain Stock off) — the "
            "thing the job builds. The materials it consumes belong in its Rate "
            "Analysis, not on the bill as work."
        ).format(where, item_code),
        title=_("Work item is a stock item"),
    )


def validate_material_item(item_code, row_idx=None):
    """Flag material being asked for against a service Item.

    A service item is never issued from a store, so a request, a transfer or a
    consumption naming one can never be fulfilled — and it is invisible to
    every stock figure the module reports.
    """
    from construction_management_suite.utils.settings import action_for, enforce

    action = action_for("material_resource_action", "Stop")
    if action == "Ignore" or not item_code:
        return
    if not is_work_item(item_code):
        return
    where = _("Row {0}: ").format(row_idx) if row_idx else ""
    enforce(
        action,
        _(
            "{0}{1} is a service item, not a material.<br><br>"
            "Material is a <b>stock</b> item — the thing a store issues. A "
            "service item is a line of work: it belongs in the work column, "
            "not in the material one."
        ).format(where, item_code),
        title=_("Material is a service item"),
    )


def validate_item_kinds(rows, material_field="item_code", work_field="work_item"):
    """Every row names material as material, and work as work.

    Both come out of the same Item master and sit side by side on a material
    row — the cement, and the plastering it is for. The pickers keep them
    apart on screen; this is what an import, an API call or an older row goes
    through. Each half reads its own severity, because they are not equally
    serious: work priced against a stock item is a mistake to be told about,
    while material that can never be issued is a document that cannot be
    fulfilled.
    """
    for row in rows:
        if work_field:
            validate_work_item(row.get(work_field), row.idx)
        if material_field:
            validate_material_item(row.get(material_field), row.idx)


def validate_material_resources(rows):
    """Every Material resource must name a real stock Item.

    A material row with no Item is silently skipped by the take-off walk, so an
    approved analysis can look complete and order nothing. Checked only as an
    analysis is approved — enforcing it on every save would make an already
    approved, already locked analysis impossible to even change the status of,
    because `validate_locked_content` refuses any resource edit once the
    analysis is behind a submitted document.
    """
    from construction_management_suite.utils.settings import action_for, enforce

    action = action_for("material_resource_action", "Stop")
    if action == "Ignore":
        return

    unnamed, not_stock = [], []
    for row in rows:
        if (row.get("resource_type") or "") != "Material":
            continue
        item = row.get("resource_item")
        if not item:
            unnamed.append(row)
        elif is_work_item(item):
            not_stock.append(row)

    if not unnamed and not not_stock:
        return

    lines = []
    for row in unnamed[:10]:
        lines.append(
            _("Row {0}: a material with no Item — qty {1} at {2}").format(
                row.idx, flt(row.get("qty")), flt(row.get("rate"))
            )
        )
    for row in not_stock[:10]:
        lines.append(
            _("Row {0}: {1} is a service item, not a material").format(
                row.idx, row.get("resource_item")
            )
        )
    why = []
    if unnamed:
        why.append(
            _("A material row with no Item is skipped by the take-off entirely.")
        )
    if not_stock:
        why.append(
            _("A service item is never issued from a store, so it cannot be ordered "
              "or consumed as material — it belongs on the bill as work.")
        )
    enforce(
        action,
        "<br>".join(lines)
        + "<br><br>"
        + " ".join(why)
        + _(" Either way this analysis would look priced and order nothing."),
        title=_("{0} material row(s) cannot be procured").format(
            len(unnamed) + len(not_stock)
        ),
    )


def validate_rate_analysis_present(rows, item_field="item_code", company=None):
    """Flag lines of work that no approved analysis can cost.

    Such a line contributes nothing to the take-off — no material is planned,
    ordered or checked against it — and the only sign today is an absence.
    """
    from construction_management_suite.api.boq import get_rate_analysis_for_item
    from construction_management_suite.utils.settings import action_for, enforce

    action = action_for("missing_rate_analysis_action")
    if action == "Ignore":
        return

    missing = []
    for row in rows:
        item = row.get(item_field)
        if not item or row.get("rate_analysis_ref"):
            continue
        if not get_rate_analysis_for_item(item, company):
            missing.append(row)
    if not missing:
        return
    enforce(
        action,
        "<br>".join(
            _("Row {0}: {1}").format(r.idx, r.get(item_field)) for r in missing[:10]
        )
        + ("<br>…" if len(missing) > 10 else "")
        + _(
            "<br><br>No approved Rate Analysis costs these, so they plan no "
            "material and will not appear in the take-off."
        )
        + (
            _(" An analysis belonging to another company does not count while "
              "the rate library is company-specific.")
            if company
            else ""
        ),
        title=_("{0} line(s) have no approved Rate Analysis").format(len(missing)),
    )


def validate_rate_analysis_company(doc, item_field="item_code"):
    """Refuse a line costed from another company's rate library.

    The picker already filters by company, so this catches what the form does
    not go through: an import, a copy of last year's bill, an amendment made
    after the document's company was changed. Left unchecked the line prices
    and budgets against rates that were never this company's.
    """
    from construction_management_suite.utils.settings import company_scoped_rates

    if not doc.get("company") or not company_scoped_rates():
        return

    wrong = []
    for row in doc.get("items") or []:
        if not row.get("rate_analysis_ref"):
            continue
        owner = frappe.db.get_value("Rate Analysis", row.rate_analysis_ref, "company")
        if owner and owner != doc.company:
            wrong.append((row, owner))
    if not wrong:
        return

    frappe.throw(
        "<br>".join(
            _("Row {0}: {1} — {2} belongs to {3}").format(
                row.idx, row.get(item_field), row.rate_analysis_ref, owner
            )
            for row, owner in wrong[:10]
        )
        + ("<br>…" if len(wrong) > 10 else "")
        + _(
            "<br><br>This document is for {0}. Pick an analysis of that company, "
            "or turn off <b>Rate Analysis Is Company-Specific</b> in Construction "
            "Settings to share one rate library across companies."
        ).format(doc.company),
        title=_("Rate Analysis belongs to another company"),
    )


# ── Cost Estimation is the cost plan; a project has exactly one ─────────────


def current_estimate(project):
    """The submitted Cost Estimation a project is costed and bought from.

    There is at most one. A superseded estimate is *cancelled*, not flagged —
    ERPNext's own amend flow is the revision mechanism — so `docstatus = 1`
    already identifies the live one with nothing extra to maintain.
    """
    if not project:
        return None
    return frappe.db.get_value(
        "Cost Estimation", {"project": project, "docstatus": 1}, "name"
    )


def validate_one_estimate_per_project(doc):
    """Refuse a second submitted estimate on a project.

    The take-off sums every submitted estimate on a project, so two would
    silently double every material quantity — the same defect a superseded BOQ
    caused before the status filter went in. Changing an estimate is a cancel
    and amend, not a second document.
    """
    if not doc.project:
        return
    other = frappe.db.get_value(
        "Cost Estimation",
        {"project": doc.project, "docstatus": 1, "name": ("!=", doc.name)},
        "name",
    )
    if not other:
        return
    frappe.throw(
        _(
            "{0} is already the cost estimation for {1}.<br><br>A project has one. "
            "To change it, cancel {0} and amend it — the amendment supersedes it "
            "and keeps the original on file. Two submitted estimates would double "
            "every material quantity in the take-off."
        ).format(frappe.utils.get_link_to_form("Cost Estimation", other), doc.project),
        title=_("This project is already estimated"),
    )


def require_estimate(project, what=None):
    """Refuse material and procurement work on a project with no cost plan.

    Two states, and they must not be confused. A project nobody has estimated
    has no plan to buy against at all. A project whose estimate is mid-amendment
    has one — it is simply cancelled for the moment — and blocking there would
    turn every routine revision into an outage across the whole job.
    """
    from construction_management_suite.utils.settings import action_for, enforce

    if not project or current_estimate(project):
        return

    subject = what or _("This document")
    amending = frappe.db.get_value(
        "Cost Estimation",
        {"project": project, "docstatus": 2},
        "name",
        order_by="modified desc",
    )
    if amending:
        # Always a warning, whatever the setting says.
        frappe.msgprint(
            _(
                "{0}'s cost estimation is being revised — {1} is cancelled and its "
                "amendment is not submitted yet. The take-off will be empty until "
                "it is."
            ).format(project, amending),
            title=_("Estimate under revision"),
            indicator="orange",
        )
        return

    enforce(
        action_for("no_estimate_action", "Stop"),
        _(
            "{0} has no Cost Estimation.<br><br>The estimate is what says which "
            "materials the work consumes, so there is nothing to plan, buy or "
            "check against. Raise one before {1}."
        ).format(project, subject),
        title=_("Project is not estimated"),
    )
