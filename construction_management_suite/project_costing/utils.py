import frappe


def calculate_daily_variance():
    """Scheduled: recalculate variance for all active project budgets."""
    budgets = frappe.get_all("Project Budget", filters={"status": "Active", "docstatus": 1})
    for b in budgets:
        try:
            doc = frappe.get_doc("Project Budget", b.name)
            doc.fetch_actual_costs()
            doc.fetch_committed_costs()
            doc.calculate_variance()
            doc.db_update()
            for item in doc.items:
                item.db_update()
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Construction: variance calc failed for {b.name}")
