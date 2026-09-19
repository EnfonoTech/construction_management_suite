import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from construction_management_suite.utils.accounting import (
    consumption_account,
    get_cost_center,
)
from construction_management_suite.utils.settings import action_for, cms_setting, enforce
from construction_management_suite.utils.validations import validate_project_company


class MaterialConsumptionEntry(Document):
    def validate(self):
        validate_project_company(self)
        for item in self.items:
            item.amount = flt(item.qty) * flt(item.valuation_rate)
        self.check_against_take_off()

    @frappe.whitelist()
    def get_items_from_forecast(self):
        """Bring in what this project is expected to consume, not yet used.

        One row per bill line rather than per material: the same cement is
        under the concrete, the mortar and the plaster, and an issue that only
        names the item can never say which of them burnt it. The line comes
        across on the row, so the take-off can be read back afterwards.

        Typing an issue from memory is how a site records cement against the
        wrong job, or a quantity nobody planned for.
        """
        if not self.project:
            frappe.throw(_("Choose the project this issue is for"))

        lines = take_off_by_line(self.project)
        attributed, loose = consumed_by_line(self.project, exclude=self.name)

        on_form = {(i.item_code, i.boq_item_ref) for i in self.items if i.item_code}
        # A row that names the item but no line covers that item on its own —
        # splitting it now would double the quantity offered.
        unsplit = {i.item_code for i in self.items if i.item_code and not i.boq_item_ref}

        added = 0
        for entry in sorted(
            lines.values(),
            key=lambda e: (e["item_code"], bill_order(e["boq_item_no"]), e["boq_item_ref"]),
        ):
            code, ref = entry["item_code"], entry["boq_item_ref"]
            outstanding = flt(entry["qty"]) - flt(attributed.get((code, ref)))

            # Issues made before the line was recorded belong to this job but to
            # no line in particular. Draw them down in bill order, so the total
            # offered still matches what the material has left overall.
            if outstanding > 0 and flt(loose.get(code)):
                absorbed = min(outstanding, flt(loose[code]))
                outstanding -= absorbed
                loose[code] -= absorbed

            if outstanding <= 0.0001 or code in unsplit or (code, ref) in on_form:
                continue

            self.append("items", {
                "item_code": code,
                "uom": entry["uom"],
                "qty": 0,
                "valuation_rate": flt(frappe.db.get_value("Item", code, "valuation_rate")),
                "boq_item_ref": ref,
                "boq_item_no": entry["boq_item_no"],
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
                "cost_center": cost_center,
                # On the row, not just the header: a report grouping Stock Entry
                # Detail by project saw nothing, and the expense defaulted to
                # Stock Adjustment, which is a variance account rather than the
                # cost of the work.
                "project": self.project,
                "expense_account": expense,
            })
        se.insert(ignore_permissions=True)
        se.submit()
        self.db_set("stock_entry_ref", se.name)
        frappe.msgprint(_("Stock Entry {0} submitted for material consumption").format(se.name))


def take_off_source(project):
    """Which document says what this job consumes.

    The Cost Estimation is the cost plan — what the work is expected to take —
    and that is what procurement and the site work to. The BOQ is the sales
    side: what the client is sold and billed. Where a project is priced both
    ways the estimate wins; where it has no estimate the bill is all there is,
    so it stands in rather than leaving the project with no plan at all.
    """
    if frappe.db.exists("Cost Estimation", {"project": project, "docstatus": 1}):
        return "Cost Estimation"
    return "BOQ"


# Both sources carry the same four columns the walk needs, under their own names.
_SOURCES = {
    "Cost Estimation": ("Cost Estimation Item", "Cost Estimation"),
    "BOQ": ("BOQ Item", "BOQ"),
}


def _take_off_rows(project, boq=None, source=None):
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
    # `boq` narrows an estimate through the bill line it was mapped from, so the
    # same filter works whichever document is being read.
    if boq:
        conditions += (
            " AND p.name = %(boq)s" if source == "BOQ"
            else " AND i.boq_item_ref IN (SELECT name FROM `tabBOQ Item` WHERE parent = %(boq)s)"
        )
    rows = frappe.db.sql(
        f"""
        SELECT i.qty, i.rate_analysis_ref, i.rate_build_up, i.item_code AS line_item,
               i.name AS line_ref, {_label_column(source)}
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
                 "rate": r.rate}
                for r in ra.resources
            ]
        for res in resources:
            if not res.get("resource_item"):
                continue
            yield row, res, flt(res.get("qty")) / output


def _label_column(source):
    """The bill's item number, whichever document is being read."""
    return "i.item_no AS line_no" if source == "BOQ" else "i.boq_item_no AS line_no"


def take_off_detail(project, boq=None, source=None):
    """Every material a project's bills are priced to consume, with its unit and rate.

    Quantities carry whatever allowance the measurer built into them; there is
    no separate waste factor to apply on top.
    """
    detail = {}
    for row, res, per_unit in _take_off_rows(project, boq, source):
        code = res.get("resource_item")
        entry = detail.setdefault(code, {
            "item_code": code,
            "uom": res.get("uom") or frappe.db.get_value("Item", code, "stock_uom"),
            "boq_qty": 0.0,
            "estimated_rate": flt(res.get("rate")),
            "boq_items": [],
        })
        entry["boq_qty"] += flt(row.qty) * per_unit
        label = row.line_no or row.line_item
        if label and label not in entry["boq_items"]:
            entry["boq_items"].append(label)
    return list(detail.values())


def take_off_by_item(project):
    """How much of each material the project is priced to consume."""
    allowed = {}
    for row, res, per_unit in _take_off_rows(project):
        code = res.get("resource_item")
        allowed[code] = allowed.get(code, 0) + flt(row.qty) * per_unit
    return allowed


def take_off_by_line(project):
    """The same allowance, split by the bill line that asks for it.

    Cement sits under concrete, under blockwork mortar and under plaster. Summed
    per item nobody can say which of those a bag was burnt on; keyed by line
    they can. Sums back to `take_off_by_item` exactly.
    """
    allowed = {}
    for row, res, per_unit in _take_off_rows(project):
        code = res.get("resource_item")
        key = (code, row.line_ref)
        entry = allowed.setdefault(key, {
            "item_code": code,
            "boq_item_ref": row.line_ref,
            "boq_item_no": row.line_no or row.line_item,
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


def consumed_by_line(project, exclude=None):
    """Issued quantity split into what names a bill line and what does not.

    Entries made before the picker carried the reference, and anything typed by
    hand, have no line against them. They are still consumption and still have
    to be netted off, so they come back separately rather than being dropped.
    """
    rows = frappe.db.sql(
        """
        SELECT i.item_code, i.boq_item_ref, SUM(i.qty) AS qty
        FROM `tabMaterial Consumption Item` i
        JOIN `tabMaterial Consumption Entry` e ON e.name = i.parent
        WHERE e.project = %(project)s AND e.docstatus = 1 AND e.name != %(exclude)s
        GROUP BY i.item_code, i.boq_item_ref
        """,
        {"project": project, "exclude": exclude or ""},
        as_dict=True,
    )
    attributed, loose = {}, {}
    for r in rows:
        if r.boq_item_ref:
            attributed[(r.item_code, r.boq_item_ref)] = flt(r.qty)
        else:
            loose[r.item_code] = loose.get(r.item_code, 0) + flt(r.qty)
    return attributed, loose


def bill_order(item_no):
    """Sort 1.9 before 1.10 — a bill numbers its items, it does not name them."""
    parts = []
    for chunk in (item_no or "").split("."):
        parts.append((0, int(chunk)) if chunk.isdigit() else (1, chunk))
    return parts
