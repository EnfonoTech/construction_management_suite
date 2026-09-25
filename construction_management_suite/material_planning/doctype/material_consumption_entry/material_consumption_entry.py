import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from construction_management_suite.utils.accounting import (
    consumption_account,
    get_cost_center,
    get_warehouse,
)
from construction_management_suite.utils.billing import stamp_accounting
from construction_management_suite.utils.settings import action_for, cms_setting, enforce
from construction_management_suite.utils.titles import project_label, set_auto_title, short_date
from construction_management_suite.utils.validations import (
    require_estimate,
    validate_item_kinds,
    validate_project_company,
    validate_uom_convertible,
)


class MaterialConsumptionEntry(Document):
    def validate(self):
        set_auto_title(self, "consumption_title",
                       [project_label(self.project), short_date(self.posting_date)])
        validate_project_company(self)
        require_estimate(self.project, _("material can be issued against it"))
        if not self.warehouse:
            self.warehouse = get_warehouse(self.project, self.company)
        validate_item_kinds(self.items)
        validate_uom_convertible(self.items)
        numbers = work_no_map(self.project) if self.project else {}
        for item in self.items:
            item.amount = flt(item.qty) * flt(item.valuation_rate)
            # Display only — the work Item is the key.
            item.work_no = numbers.get(item.work_item)
        self.check_against_take_off()

    @frappe.whitelist()
    def get_items_from_site_report(self):
        """Bring across what the day's site report says was used.

        The report already records the item, the quantity, the batch and the
        store. Retyping it into the issue is how the two disagree, and the
        reference between them existed with nothing reading it.

        A row is attributed to a bill line only where the material serves
        exactly one — cement under three lines cannot be split by a site diary
        that never named one.
        """
        if not self.daily_site_report_ref:
            frappe.throw(_("Choose the site report this issue comes from"))

        lines = take_off_by_line(self.project) if self.project else {}
        single = {}
        for (code, work_item), entry in lines.items():
            single[code] = None if code in single else work_item
        numbers = work_no_map(self.project) if self.project else {}

        on_form = {i.item_code for i in self.items if i.item_code}
        added = 0
        for row in frappe.get_all(
            "Site Report Material",
            filters={"parent": self.daily_site_report_ref},
            fields=["item_code", "uom", "qty_used", "batch_no"],
            order_by="idx asc",
        ):
            if not row.item_code or row.item_code in on_form or flt(row.qty_used) <= 0:
                continue
            work_item = single.get(row.item_code)
            self.append("items", {
                "item_code": row.item_code,
                "uom": row.uom or frappe.db.get_value("Item", row.item_code, "stock_uom"),
                "qty": flt(row.qty_used),
                "batch_no": row.batch_no,
                "valuation_rate": flt(frappe.db.get_value("Item", row.item_code, "valuation_rate")),
                "work_item": work_item,
                "work_no": numbers.get(work_item),
            })
            added += 1
        return added

    def check_against_take_off(self):
        """Flag consuming more of a material than the bill was priced to need.

        The take-off is the figure the BOQ Resource Analysis report shows: every
        submitted BOQ line's quantity times what its analysis says the line
        consumes per unit. Going past it is not an error — a
        variation adds work and breakage happens — but it is the moment a job
        starts eating its margin, and nothing said so before.

        Measured per material across the whole entry, not per row: the picker
        now writes a row per bill line, and three rows of cement under the
        allowance each would pass while the bag count went well past it.
        """
        action = action_for("consumption_over_takeoff_action")
        if action == "Ignore" or not self.project:
            return
        tolerance = flt(cms_setting("consumption_tolerance_percent", 0))

        allowed = take_off_by_item(self.project)
        if not allowed:
            return
        consumed = consumed_by_item(self.project, exclude=self.name)

        this_entry = {}
        for item in self.items:
            if item.item_code:
                this_entry[item.item_code] = flt(this_entry.get(item.item_code)) + flt(item.qty)

        over = []
        for code, qty in sorted(this_entry.items()):
            budget = flt(allowed.get(code))
            if not budget:
                continue
            total = flt(consumed.get(code)) + qty
            if total <= budget * (1 + tolerance / 100):
                continue
            over.append((code, total, budget))

        if not over:
            return
        enforce(
            action,
            "<br>".join(
                _("{0}: {1} used against a take-off of {2}").format(
                    code, flt(total, 3), flt(budget, 3)
                )
                for code, total, budget in over[:10]
            ),
            title=_("{0} material(s) past the take-off").format(len(over)),
        )

    def before_submit(self):
        self.status = "Submitted"

    def on_submit(self):
        self._create_stock_entry()

    def before_cancel(self):
        self.status = "Cancelled"

    def on_cancel(self):
        self._cancel_stock_entry()

    def _cancel_stock_entry(self):
        if not self.stock_entry_ref:
            return
        se = frappe.get_doc("Stock Entry", self.stock_entry_ref)
        if se.docstatus == 1:
            se.cancel()
            frappe.msgprint(_("Stock Entry {0} cancelled").format(se.name))

    def _create_stock_entry(self):
        se = frappe.new_doc("Stock Entry")
        # Site consumption is a plain issue — "…for Manufacture" belongs to Work Orders.
        se.stock_entry_type = "Material Issue"
        se.company = self.company
        se.posting_date = self.posting_date
        se.project = self.project
        se.cms_consumption_ref = self.name
        cost_center = get_cost_center(self.project, self.company)
        expense = consumption_account(self.company)
        for item in self.items:
            se.append("items", {
                "item_code": item.item_code,
                "qty": flt(item.qty),
                "uom": item.uom,
                "s_warehouse": self.warehouse,
                "batch_no": item.batch_no,
                # Say it on the row rather than inheriting it. Stock Entry
                # Detail only reads batch_no when this is set, and it defaults
                # from a Stock Settings checkbox — so on a site with that box
                # off, the batch a storeman chose here was silently dropped.
                "use_serial_batch_fields": 1,
                "cost_center": cost_center,
                # On the row, not just the header: a report grouping Stock Entry
                # Detail by project saw nothing, and the expense defaulted to
                # Stock Adjustment, which is a variance account rather than the
                # cost of the work.
                "project": self.project,
                "expense_account": expense,
                # The movement says which work burnt it, not just which project.
                "cms_work_item": item.work_item,
            })
        stamp_accounting(se, project=self.project, cost_center=cost_center)
        se.insert(ignore_permissions=True)
        se.submit()
        self.db_set("stock_entry_ref", se.name)
        frappe.msgprint(_("Stock Entry {0} submitted for material consumption").format(se.name))


def take_off_source(project):
    """Which document says what this job consumes.

    Always the Cost Estimation. It is the cost plan — what the work is expected
    to take — and it is what procurement, the site and the budget work to.

    The BOQ is the sales side: what the client is sold and billed, at quantities
    and rates that carry margin and may be front-loaded. Buying off it orders
    the wrong quantities, so there is deliberately no fallback. A project with
    no estimate has no plan, and `require_estimate` says so rather than
    substituting the bill silently.
    """
    return "Cost Estimation"


# Both sources carry the same four columns the walk needs, under their own names.
_SOURCES = {
    "Cost Estimation": ("Cost Estimation Item", "Cost Estimation"),
    "BOQ": ("BOQ Item", "BOQ"),
}


# A revision does not cancel the bill it replaces: `create_revision` leaves the
# old BOQ at docstatus 1 with status 'Revised'. Filtering on docstatus alone
# therefore counts every revision of a project's bill — doubling the take-off
# and listing every line of work twice in the site diary's picker.
#
# Cost Estimation needs no equivalent. It is replaced by cancel-and-amend, which
# leaves the superseded document at docstatus 2, already excluded.
SUPERSEDED_STATUS = {"BOQ": "Revised"}


def _live_only(parent, alias="p"):
    """SQL fragment excluding documents a later revision has superseded."""
    status = SUPERSEDED_STATUS.get(parent)
    if not status:
        return ""
    return " AND {0}.status != {1}".format(alias, frappe.db.escape(status))


# Only what a store issues. Labour, plant, overhead and work let to a trade are
# real costs and belong in the estimate, but nobody buys them into a warehouse,
# and offering them as material to order is how a subcontracted package gets
# bought twice.
STOCK_TYPES = ("Material",)


def work_lines(project):
    """Every line of work on a project's cost estimate.

    Keyed by `item_code` — the work Item. That is the same key the take-off, the
    site diary and every buying document use, so what the site reports progress
    against is exactly what the materials were planned from.
    """
    child, parent = _SOURCES[take_off_source(project)]
    return frappe.db.sql(
        f"""
        SELECT i.item_code AS work_item, i.item_code, i.description,
               i.qty, i.uom
        FROM `tab{child}` i
        JOIN `tab{parent}` p ON p.name = i.parent
        WHERE p.project = %s AND p.docstatus = 1{_live_only(parent)}
        ORDER BY i.idx
        """,
        project,
        as_dict=True,
    )


def work_no_map(project):
    """Work Item -> the number the client speaks, from the bill.

    The estimate has no item numbers; the BOQ does, and a QS quotes them in
    every letter. Display only — nothing keys on it.
    """
    rows = frappe.db.sql(
        """
        SELECT i.item_code, i.item_no
        FROM `tabBOQ Item` i JOIN `tabBOQ` b ON b.name = i.parent
        WHERE b.project = %s AND b.docstatus = 1 AND b.status != 'Revised'
        """,
        project,
        as_dict=True,
    )
    return {r.item_code: r.item_no for r in rows if r.item_no}


def _take_off_rows(project, boq=None, source=None, types=STOCK_TYPES):
    """Walk a project's priced lines and yield the resources under each.

    Frozen `rate_build_up` where a line has one, the live analysis otherwise —
    the same precedence the BOQ Resource Analysis report uses. Everything that
    answers "what is this job supposed to consume" reads through here, so the
    forecast, the consumption check and that report cannot disagree.

    Yields (line, resource, per_unit), where per_unit is the resource quantity
    for one unit of the line.
    """
    source = source or take_off_source(project)
    child, parent = _SOURCES[source]

    conditions = "p.project = %(project)s AND p.docstatus = 1"
    params = {"project": project, "boq": boq or ""}
    if boq:
        # Narrowing to one bill. Naming it explicitly pins one document, so the
        # superseded filter is both redundant and wrong here — asking for a
        # revised bill by name is a deliberate choice. Against the estimate the
        # bill narrows by the work Items it prices, which is the same key both
        # documents use.
        conditions += (
            " AND p.name = %(boq)s" if source == "BOQ"
            else " AND i.item_code IN (SELECT item_code FROM `tabBOQ Item` WHERE parent = %(boq)s)"
        )
    else:
        conditions += _live_only(parent)
    rows = frappe.db.sql(
        f"""
        SELECT i.qty, i.rate_analysis_ref, i.rate_build_up,
               i.item_code AS work_item, i.item_code AS line_item
        FROM `tab{child}` i
        JOIN `tab{parent}` p ON p.name = i.parent
        WHERE {conditions}
        """,
        params,
        as_dict=True,
    )
    for row in rows:
        resources, output = [], 1
        if row.rate_build_up:
            try:
                frozen = json.loads(row.rate_build_up)
            except (ValueError, TypeError):
                frozen = None
            if frozen and frozen.get("resources"):
                resources = frozen["resources"]
                output = flt(frozen.get("output_qty")) or 1
        if not resources and row.rate_analysis_ref:
            if not frappe.db.exists("Rate Analysis", row.rate_analysis_ref):
                continue
            ra = frappe.get_cached_doc("Rate Analysis", row.rate_analysis_ref)
            output = flt(ra.output_qty) or 1
            resources = [
                {"resource_item": r.resource_item, "qty": r.qty, "uom": r.uom,
                 "rate": r.rate, "resource_type": r.resource_type}
                for r in ra.resources
            ]
        for res in resources:
            if not res.get("resource_item"):
                continue
            # A frozen build-up stores the type under "type"; a live analysis
            # under "resource_type".
            rtype = res.get("type") or res.get("resource_type")
            if types and rtype and rtype not in types:
                continue
            yield row, res, flt(res.get("qty")) / output


def take_off_detail(project, boq=None, source=None, types=STOCK_TYPES):
    """Every material a project's bills are priced to consume, with its unit and rate.

    Quantities carry whatever allowance the measurer built into them; there is
    no separate waste factor to apply on top.
    """
    detail = {}
    for row, res, per_unit in _take_off_rows(project, boq, source, types):
        code = res.get("resource_item")
        entry = detail.setdefault(code, {
            "item_code": code,
            "uom": res.get("uom") or frappe.db.get_value("Item", code, "stock_uom"),
            "boq_qty": 0.0,
            "estimated_rate": flt(res.get("rate")),
            "work_items": [],
        })
        entry["boq_qty"] += flt(row.qty) * per_unit
        if row.work_item and row.work_item not in entry["work_items"]:
            entry["work_items"].append(row.work_item)
    return list(detail.values())


def take_off_by_item(project):
    """How much of each material the project is priced to consume."""
    allowed = {}
    for row, res, per_unit in _take_off_rows(project):
        code = res.get("resource_item")
        allowed[code] = allowed.get(code, 0) + flt(row.qty) * per_unit
    return allowed


def take_off_by_line(project, boq=None):
    """The same allowance, split by the line of work that asks for it.

    Cement sits under concrete, under blockwork mortar and under plaster. Summed
    per item nobody can say which of those a bag was burnt on; keyed by work
    item they can. Sums back to `take_off_by_item` exactly.

    Keyed `(material, work item)`. Both halves are Item codes, so the key
    survives a bill being revised — which a child-row name did not.
    """
    allowed = {}
    for row, res, per_unit in _take_off_rows(project, boq):
        code = res.get("resource_item")
        key = (code, row.work_item)
        entry = allowed.setdefault(key, {
            "item_code": code,
            "work_item": row.work_item,
            "uom": res.get("uom") or frappe.db.get_value("Item", code, "stock_uom"),
            "estimated_rate": flt(res.get("rate")),
            "qty": 0.0,
        })
        entry["qty"] += flt(row.qty) * per_unit
    return allowed


def consumed_by_item(project, exclude=None):
    """Quantity already issued to the project on submitted consumption entries."""
    rows = frappe.db.sql(
        """
        SELECT i.item_code, SUM(i.qty) AS qty
        FROM `tabMaterial Consumption Item` i
        JOIN `tabMaterial Consumption Entry` e ON e.name = i.parent
        WHERE e.project = %(project)s AND e.docstatus = 1 AND e.name != %(exclude)s
        GROUP BY i.item_code
        """,
        {"project": project, "exclude": exclude or ""},
        as_dict=True,
    )
    return {r.item_code: flt(r.qty) for r in rows}


