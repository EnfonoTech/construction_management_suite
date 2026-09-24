from frappe import _


def get_data():
    """What is built on this estimate.

    Only the Project Budget carries a link to the estimate itself — everything
    else downstream hangs off the project, which a connections panel cannot
    filter by. Those are reached from the Create and View groups on the form
    instead, and the headline says where the job stands against the plan.
    """
    return {
        "fieldname": "cost_estimation_ref",
        "transactions": [
            {"label": _("Costing"), "items": ["Project Budget"]},
        ],
    }
