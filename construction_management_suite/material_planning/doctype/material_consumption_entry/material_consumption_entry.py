import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from construction_management_suite.utils.accounting import (
    consumption_account,
    ensure_project_cost_center,
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

    def check_against_take_off(self):
        """Flag consuming more of a material than the bill was priced to need.

        The take-off is the figure the BOQ Resource Analysis report shows: every
        submitted BOQ line's quantity times what its analysis says the line
        consumes per unit, waste included. Going past it is not an error — a
        variation adds work and breakage happens — but it is the moment a job
        starts eating its margin, and nothing said so before.
        """
        action = action_for("consumption_over_takeoff_action")
        if action == "Ignore" or not self.project:
            return
        tolerance = flt(cms_setting("consumption_tolerance_percent", 0))

        allowed = take_off_by_item(self.project)
        if not allowed:
            return
        consumed = consumed_by_item(self.project, exclude=self.name)

        over = []
        for item in self.items:
            budget = flt(allowed.get(item.item_code))
            if not budget:
                continue
            total = flt(consumed.get(item.item_code)) + flt(item.qty)
            if total <= budget * (1 + tolerance / 100):
                continue
            over.append((item, total, budget))

        if not over:
            return
        enforce(
            action,
            "<br>".join(
                _("Row {0} ({1}): {2} used against a take-off of {3}").format(
                    i.idx, i.item_code, flt(total, 3), flt(budget, 3)
                )
                for i, total, budget in over[:10]
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
        ensure_project_cost_center(self.project, self.company)
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


def take_off_by_item(project):
    """How much of each material the project's bills were priced to consume.

    Frozen build-up where a line has one, live analysis otherwise — the same
    precedence as the BOQ Resource Analysis report, so the two can never
    disagree about what was allowed.
    """
    import json

    allowed = {}
    rows = frappe.db.sql(
        """
        SELECT i.qty, i.rate_analysis_ref, i.rate_build_up
        FROM `tabBOQ Item` i
        JOIN `tabBOQ` b ON b.name = i.parent
        WHERE b.project = %s AND b.docstatus = 1
        """,
        project,
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
                {"resource_item": r.resource_item, "qty": r.qty, "waste_factor": r.waste_factor}
                for r in ra.resources
            ]
        for res in resources:
            item_code = res.get("resource_item")
            if not item_code:
                continue
            per_unit = flt(res.get("qty")) / output
            allowed[item_code] = allowed.get(item_code, 0) + (
                flt(row.qty) * per_unit * (1 + flt(res.get("waste_factor")) / 100)
            )
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
