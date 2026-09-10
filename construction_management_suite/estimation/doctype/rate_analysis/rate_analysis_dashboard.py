from frappe import _


def get_data():
    """Where this analysis has been used to price work."""
    return {
        "fieldname": "rate_analysis_ref",
        "transactions": [
            {"label": _("Costing"), "items": ["BOQ", "Cost Estimation"]},
        ],
    }
