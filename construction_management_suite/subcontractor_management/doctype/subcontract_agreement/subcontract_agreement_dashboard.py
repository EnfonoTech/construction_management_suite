from frappe import _


def get_data():
    """Everything raised against this agreement.

    The Purchase Order links back with `cms_subcontract_ref`; the orders and
    certificates use their own `subcontract_agreement` field, which is what
    non_standard_fieldnames is for.
    """
    return {
        "fieldname": "cms_subcontract_ref",
        "non_standard_fieldnames": {
            "Subcontractor Work Order": "subcontract_agreement",
            "Subcontractor Payment Certificate": "subcontract_agreement",
        },
        "transactions": [
            {"label": _("Delivery"), "items": ["Subcontractor Work Order"]},
            {"label": _("Payment"), "items": ["Subcontractor Payment Certificate"]},
            {"label": _("Commitment"), "items": ["Purchase Order"]},
        ],
    }
