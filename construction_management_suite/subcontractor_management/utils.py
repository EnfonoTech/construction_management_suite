import frappe
from frappe.utils import flt, nowdate, add_days


def generate_aging_report():
    """Scheduled weekly: log subcontractor payment aging summary."""
    try:
        overdue = frappe.db.sql(
            """
            SELECT
                sca.subcontractor,
                SUM(sca.subcontract_value - sca.total_paid) AS outstanding
            FROM `tabSubcontract Agreement` sca
            WHERE sca.docstatus = 1
              AND sca.status = 'Active'
            GROUP BY sca.subcontractor
            HAVING outstanding > 0
            ORDER BY outstanding DESC
            """,
            as_dict=True,
        )
        if overdue:
            frappe.logger("cms").info(
                f"Subcontractor Aging: {len(overdue)} suppliers with outstanding payments"
            )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Construction: subcontractor aging report failed")
