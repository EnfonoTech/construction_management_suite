"""
BOQ public API endpoints — called from JS / mobile / external integrations.
All methods are whitelisted for Frappe REST exposure.
"""

import frappe
from frappe import _
from frappe.utils import flt


@frappe.whitelist()
def get_boq_summary(project):
    """Return latest approved BOQ summary for a project."""
    boqs = frappe.get_all(
        "BOQ",
        filters={"project": project, "docstatus": 1},
        fields=["name", "boq_title", "grand_total", "currency", "revision_no", "status"],
        order_by="revision_no desc",
        limit=1,
    )
    if not boqs:
        return {}
    boq = boqs[0]
    boq["items_count"] = frappe.db.count("BOQ Item", {"parent": boq["name"]})
    return boq


@frappe.whitelist()
def apply_rate_analysis_to_boq(rate_analysis, boq, item_code):
    """Push Rate Analysis rates into matching BOQ Item rows."""
    ra = frappe.get_doc("Rate Analysis", rate_analysis)
    boq_doc = frappe.get_doc("BOQ", boq)
    updated = 0
    for item in boq_doc.items:
        if item.item_code == item_code:
            item.rate = flt(ra.rate_per_unit)
            item.material_rate = flt(ra.total_material_cost) / (flt(ra.output_qty) or 1)
            item.labour_rate = flt(ra.total_labour_cost) / (flt(ra.output_qty) or 1)
            item.equipment_rate = flt(ra.total_equipment_cost) / (flt(ra.output_qty) or 1)
            item.rate_analysis_ref = rate_analysis
            updated += 1
    boq_doc.calculate_item_amounts()
    boq_doc.calculate_totals()
    boq_doc.save(ignore_permissions=True)
    return _("{0} BOQ item(s) updated with rate analysis {1}").format(updated, rate_analysis)


@frappe.whitelist()
def get_project_cost_dashboard(project):
    """Return aggregated cost KPIs for a project dashboard widget."""
    budget = frappe.db.get_value(
        "Project Budget",
        {"project": project, "docstatus": 1},
        ["total_budget", "total_actual_cost", "budget_utilization_percent", "variance_amount"],
        as_dict=True,
    ) or {}

    billing = frappe.db.sql(
        """
        SELECT
            SUM(gross_amount_this_period) AS total_billed,
            SUM(net_payable_this_period) AS total_net_billed,
            COUNT(*) AS ipc_count
        FROM `tabInterim Payment Certificate`
        WHERE project = %s AND docstatus = 1
        """,
        project,
        as_dict=True,
    )[0] or {}

    subcontract = frappe.db.sql(
        """
        SELECT SUM(subcontract_value) AS total_subcontract
        FROM `tabSubcontract Agreement`
        WHERE project = %s AND docstatus = 1 AND status != 'Terminated'
        """,
        project,
        as_dict=True,
    )[0] or {}

    return {
        "budget": budget,
        "billing": billing,
        "subcontract": subcontract,
        "retention": get_retention_summary(project),
    }


@frappe.whitelist()
def get_site_progress_timeline(project, limit=30):
    """Return daily site report progress timeline for Gantt/chart display."""
    return frappe.db.sql(
        """
        SELECT report_date, cumulative_percent_complete, weather_condition, safety_incidents
        FROM `tabDaily Site Report`
        WHERE project = %s AND docstatus = 1
        ORDER BY report_date DESC
        LIMIT %s
        """,
        (project, int(limit)),
        as_dict=True,
    )


@frappe.whitelist()
def create_material_request_from_forecast(forecast_name):
    """Convert a Material Forecast into ERPNext Material Request(s)."""
    forecast = frappe.get_doc("Material Forecast", forecast_name)
    if forecast.docstatus != 1:
        frappe.throw(_("Material Forecast must be submitted first"))

    mr = frappe.new_doc("Material Request")
    mr.material_request_type = "Purchase"
    mr.company = forecast.company
    mr.transaction_date = frappe.utils.nowdate()
    mr.schedule_date = forecast.to_date or frappe.utils.add_months(mr.transaction_date, 1)

    for item in forecast.items:
        if flt(item.qty_to_order) > 0:
            mr.append("items", {
                "item_code": item.item_code,
                "qty": flt(item.qty_to_order),
                "uom": item.uom,
                "warehouse": item.warehouse,
                "project": forecast.project,
                "schedule_date": item.required_by_date or mr.schedule_date,
            })

    if not mr.items:
        frappe.msgprint(_("No items require ordering — all quantities already covered"))
        return None

    mr.insert(ignore_permissions=True)
    frappe.msgprint(_("Material Request {0} created").format(mr.name))
    return mr.name


@frappe.whitelist()
def get_retention_summary(project):
    """Return retention held vs released for a project."""
    held = frappe.db.sql(
        """
        SELECT SUM(retention_amount) AS total_held
        FROM `tabInterim Payment Certificate`
        WHERE project = %s AND docstatus = 1
        """,
        project,
        as_dict=True,
    )[0].get("total_held") or 0

    released = frappe.db.sql(
        """
        SELECT SUM(release_amount) AS total_released
        FROM `tabRetention Release`
        WHERE project = %s AND docstatus = 1
        """,
        project,
        as_dict=True,
    )[0].get("total_released") or 0

    return {
        "total_held": flt(held),
        "total_released": flt(released),
        "net_retention": flt(held) - flt(released),
    }


@frappe.whitelist()
def get_previous_ipc_position(project):
    """Where the last certificate on this project left off.

    Saves the billing officer looking up the previous certificate by hand —
    getting the cumulative figure wrong is how a client gets double-billed.
    """
    row = frappe.db.sql(
        """
        SELECT SUM(gross_amount_this_period) AS cumulative_amount,
               MAX(ipc_number) AS last_ipc_number,
               COUNT(*) AS certificates
        FROM `tabInterim Payment Certificate`
        WHERE project = %s AND docstatus = 1
        """,
        project,
        as_dict=True,
    )[0]
    return {
        "cumulative_amount": flt(row.cumulative_amount),
        "certificates": row.certificates or 0,
        "next_ipc_number": (row.last_ipc_number or 0) + 1,
    }
