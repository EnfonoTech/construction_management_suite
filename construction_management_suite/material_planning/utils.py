import frappe


def recompute_forecasts():
    """Scheduled daily: refresh open forecasts against the latest Purchase Orders."""
    forecasts = frappe.get_all(
        "Material Forecast",
        filters={"status": ["in", ["Draft", "Approved"]], "docstatus": 1},
    )
    for f in forecasts:
        try:
            doc = frappe.get_doc("Material Forecast", f.name)
            doc.recalculate()
            doc.db_update()
            for item in doc.items:
                item.db_update()
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Construction: forecast recompute failed for {f.name}")
