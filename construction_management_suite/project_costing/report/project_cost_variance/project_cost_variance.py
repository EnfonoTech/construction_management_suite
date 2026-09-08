import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
    filters = filters or {}
    columns = get_columns()
    data = get_data(filters)
    chart = get_chart(data)
    return columns, data, None, chart


def get_columns():
    return [
        {"label": _("Project"), "fieldname": "project", "fieldtype": "Link", "options": "Project", "width": 150},
        {"label": _("Budget Title"), "fieldname": "budget_title", "fieldtype": "Data", "width": 180},
        {"label": _("Cost Head"), "fieldname": "cost_head", "fieldtype": "Data", "width": 180},
        {"label": _("Category"), "fieldname": "cost_category", "fieldtype": "Data", "width": 110},
        {"label": _("Budgeted"), "fieldname": "budgeted_amount", "fieldtype": "Currency", "width": 140},
        {"label": _("Actual"), "fieldname": "actual_amount", "fieldtype": "Currency", "width": 140},
        {"label": _("Committed"), "fieldname": "committed_amount", "fieldtype": "Currency", "width": 140},
        {"label": _("Variance"), "fieldname": "variance", "fieldtype": "Currency", "width": 140},
        {"label": _("Utilization %"), "fieldname": "utilization_pct", "fieldtype": "Percent", "width": 110},
    ]


def get_data(filters):
    conditions = []
    params = {}
    if filters.get("project"):
        conditions.append("pb.project = %(project)s")
        params["project"] = filters["project"]
    if filters.get("company"):
        conditions.append("pb.company = %(company)s")
        params["company"] = filters["company"]

    where = ("AND " + " AND ".join(conditions)) if conditions else ""
    rows = frappe.db.sql(
        f"""
        SELECT
            pb.project, pb.name AS budget_title,
            pbi.cost_head, pbi.cost_category,
            pbi.budgeted_amount, pbi.actual_amount, pbi.committed_amount,
            (pbi.budgeted_amount - pbi.actual_amount) AS variance,
            CASE WHEN pbi.budgeted_amount > 0
                THEN pbi.actual_amount / pbi.budgeted_amount * 100
                ELSE 0 END AS utilization_pct
        FROM `tabProject Budget` pb
        JOIN `tabProject Budget Item` pbi ON pbi.parent = pb.name
        WHERE pb.docstatus = 1 {where}
        ORDER BY pb.project, pbi.cost_category
        """,
        params,
        as_dict=True,
    )
    return rows


def get_chart(data):
    if not data:
        return None
    top = sorted(data, key=lambda r: flt(r.get("actual_amount", 0)), reverse=True)[:8]
    return {
        "data": {
            "labels": [r["cost_head"] for r in top],
            "datasets": [
                {"name": _("Budget"), "values": [flt(r["budgeted_amount"]) for r in top]},
                {"name": _("Actual"), "values": [flt(r["actual_amount"]) for r in top]},
            ],
        },
        "type": "bar",
    }
