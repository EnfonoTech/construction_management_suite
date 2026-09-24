import frappe
from frappe import _
from frappe.utils import add_days, flt, getdate


def execute(filters=None):
    filters = filters or {}
    columns = get_columns()
    data = get_data(filters)
    chart = get_chart(data)
    return columns, data, None, chart


def get_columns():
    return [
        {"label": _("BOQ"), "fieldname": "name", "fieldtype": "Link", "options": "BOQ", "width": 130},
        {"label": _("Title"), "fieldname": "boq_title", "fieldtype": "Data", "width": 200},
        {"label": _("Project"), "fieldname": "project", "fieldtype": "Link", "options": "Project", "width": 130},
        {"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 100},
        {"label": _("Client"), "fieldname": "client", "fieldtype": "Link", "options": "Customer", "width": 130},
        {"label": _("Currency"), "fieldname": "currency", "fieldtype": "Link", "options": "Currency", "width": 80},
        {"label": _("Material"), "fieldname": "total_material_amount", "fieldtype": "Currency", "options": "currency", "width": 130},
        {"label": _("Labour"), "fieldname": "total_labour_amount", "fieldtype": "Currency", "options": "currency", "width": 130},
        {"label": _("Equipment"), "fieldname": "total_equipment_amount", "fieldtype": "Currency", "options": "currency", "width": 130},
        {"label": _("Overhead"), "fieldname": "total_overhead_amount", "fieldtype": "Currency", "options": "currency", "width": 130},
        {"label": _("Total Amount"), "fieldname": "total_amount", "fieldtype": "Currency", "options": "currency", "width": 150},
        {"label": _("Grand Total"), "fieldname": "grand_total", "fieldtype": "Currency", "options": "currency", "width": 150},
        {"label": _("Revision No"), "fieldname": "revision_no", "fieldtype": "Int", "width": 90},
    ]


def get_data(filters):
    conditions = []
    params = {}

    if filters.get("project"):
        conditions.append("project = %(project)s")
        params["project"] = filters["project"]
    if filters.get("company"):
        conditions.append("company = %(company)s")
        params["company"] = filters["company"]
    if filters.get("status"):
        conditions.append("status = %(status)s")
        params["status"] = filters["status"]
    if filters.get("from_date"):
        conditions.append("creation >= %(from_date)s")
        params["from_date"] = filters["from_date"]
    if filters.get("to_date"):
        # `creation` carries a time; a bare date would drop everything raised
        # on the last day of the range, which is the day people ask about.
        conditions.append("creation < %(to_date)s")
        params["to_date"] = add_days(getdate(filters["to_date"]), 1)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    return frappe.db.sql(
        f"""
        SELECT name, boq_title, project, status, client, currency,
               total_material_amount, total_labour_amount, total_equipment_amount,
               total_overhead_amount, total_amount, grand_total, revision_no
        FROM `tabBOQ`
        {where}
        ORDER BY project, revision_no DESC
        """,
        params,
        as_dict=True,
    )


def get_chart(data):
    if not data:
        return None
    labels = [d.get("boq_title", d.get("name", "")) for d in data[:10]]
    material = [flt(d.get("total_material_amount")) for d in data[:10]]
    labour = [flt(d.get("total_labour_amount")) for d in data[:10]]
    equipment = [flt(d.get("total_equipment_amount")) for d in data[:10]]
    return {
        "data": {
            "labels": labels,
            "datasets": [
                {"name": _("Material"), "values": material},
                {"name": _("Labour"), "values": labour},
                {"name": _("Equipment"), "values": equipment},
            ],
        },
        "type": "bar",
        "barOptions": {"stacked": True},
    }
