from frappe import _


def get_dashboard_data(data):
    """Add the construction documents to a Project's Connections tab.

    Frappe passes ERPNext's own Project dashboard in and expects it back, so
    this extends the existing groups rather than replacing them.
    """
    data = data or {}
    data.setdefault("transactions", [])
    data.setdefault("non_standard_fieldnames", {})

    # Site Transfer records the receiving job, not a plain `project` link.
    data["non_standard_fieldnames"]["Site Transfer"] = "to_project"

    data["transactions"].extend(
        [
            {"label": _("Contract"), "items": ["BOQ", "Variation Order"]},
            {"label": _("Estimating"), "items": ["Cost Estimation", "Project Budget"]},
            {"label": _("Progress Billing"), "items": ["Interim Payment Certificate", "Retention Release"]},
            {
                "label": _("Subcontracting"),
                "items": [
                    "Subcontract Agreement",
                    "Subcontractor Work Order",
                    "Subcontractor Payment Certificate",
                ],
            },
            {"label": _("Site"), "items": ["Daily Site Report", "Site Material Request"]},
            {
                "label": _("Material Planning"),
                "items": ["Material Forecast", "Site Transfer", "Material Consumption Entry"],
            },
        ]
    )
    return data
