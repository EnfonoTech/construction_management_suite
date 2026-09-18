from frappe import _


def get_data():
    """What this forecast turned into.

    The Material Request links back with `cms_forecast_ref`; this document holds
    only its name as text, so cancelling the request cannot cancel the forecast.
    """
    return {
        "fieldname": "cms_forecast_ref",
        "transactions": [
            {"label": _("Procurement"), "items": ["Material Request"]},
        ],
    }
