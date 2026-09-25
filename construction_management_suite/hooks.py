app_name = "construction_management_suite"
app_title = "Construction Management Suite"
app_publisher = "Your Company"
app_description = "Production-ready Construction Management Suite for ERPNext/Frappe"
app_email = "admin@construction.com"
app_license = "MIT"
app_version = "1.0.0"

# Required apps
required_apps = ["frappe", "erpnext"]

# Includes in <head>
app_include_css = "/assets/construction_management_suite/css/cms.css"
app_include_js = "/assets/construction_management_suite/js/cms.js"

# DocType permissions and custom fields
fixtures = [
    {"dt": "Custom Field", "filters": [["fieldname", "like", "cms_%"]]},
    {"dt": "Property Setter", "filters": [["doc_type", "in", [
        "BOQ", "Cost Estimation", "Project Budget", "Daily Site Report",
        "Interim Payment Certificate", "Subcontract Agreement",
        "Material Forecast", "Site Transfer",
    ]]]},
    {"dt": "Role", "filters": [["role_name", "in", [
        "Construction Admin",
        "Construction Project Manager",
        "Construction Site Engineer",
        "Construction Quantity Surveyor",
        "Construction Subcontractor",
        "Construction Billing Officer",
        "Construction Viewer",
    ]]]},
]

# NOTE: the workspace is deliberately NOT a fixture. It is a standard workspace
# owned by the BOQ Management module, and frappe syncs it from that JSON on every
# migrate. Exporting it here as well put two files in charge of one page — the
# fixture loader imports every .json in `fixtures/`, whatever this list says —
# and the loser is whichever wrote first. Edit
# boq_management/workspace/construction_management_suite/ and nothing else.

# Document Events
# NOTE: CMS's own doctypes must NOT be registered here. Frappe already calls the
# controller's on_submit/on_cancel; adding a hook that re-calls it runs every
# side effect twice (duplicate invoices, duplicate stock entries).
# Only doctypes this app does NOT own. Hooking one of our own here as well as
# in its controller runs the same code twice — that is what emptied this dict
# once already.
_PROJECT_WAREHOUSE = (
    "construction_management_suite.project_costing.purchase_controls.set_project_warehouse"
)
# Budget, estimated rate, and whether the plan includes what is being bought.
# On every buying document, not the order alone: a Purchase Invoice with Update
# Stock is a purchase and a receipt in one, and it was checked against nothing.
_PURCHASE_CONTROLS = (
    "construction_management_suite.project_costing.purchase_controls.validate_purchase_document"
)

doc_events = {
    "Purchase Order": {
        "before_validate": _PROJECT_WAREHOUSE,
        "validate": _PURCHASE_CONTROLS,
    },
    # The same questions on every document that receives or asks for material
    # against a job. before_validate for the warehouse, because ERPNext refuses
    # a blank one in its own validate — see set_project_warehouse.
    "Material Request": {"before_validate": _PROJECT_WAREHOUSE, "validate": _PURCHASE_CONTROLS},
    "Purchase Receipt": {"before_validate": _PROJECT_WAREHOUSE, "validate": _PURCHASE_CONTROLS},
    "Purchase Invoice": {
        "before_validate": _PROJECT_WAREHOUSE,
        "validate": _PURCHASE_CONTROLS,
        # A subcontract order can never be received, so being billed in full is
        # the only thing that can finish it.
        "on_submit": "construction_management_suite.project_costing.purchase_controls.close_subcontract_order",
    },
}

# Scheduled Tasks
scheduler_events = {
    "daily": [
        # Auto-calculate project cost variance
        "construction_management_suite.project_costing.utils.calculate_daily_variance",
        # Alert on retention release eligibility
        "construction_management_suite.progress_billing.utils.check_retention_release",
        # Daily material forecast recompute
        "construction_management_suite.material_planning.utils.recompute_forecasts",
    ],
    "weekly": [
        # Subcontractor aging report
        "construction_management_suite.subcontractor_management.utils.generate_aging_report",
    ],
}

# Override Whitelisted Methods (used by API)
override_whitelisted_methods = {}

# Jinja environment extensions for print formats
jinja = {
    "methods": [
        "construction_management_suite.utils.helpers.format_currency_arabic",
        "construction_management_suite.utils.helpers.number_to_words_arabic",
        "construction_management_suite.utils.helpers.get_project_summary",
    ],
    "filters": [
        "construction_management_suite.utils.helpers.arabic_number",
    ],
}

# Regional overrides (loaded by localization modules)
regional_overrides = {
    "Saudi Arabia": {
        "construction_management_suite.localization.ksa.vat": "construction_management_suite.localization.ksa.overrides.get_vat_settings",
    },
    "United Arab Emirates": {
        "construction_management_suite.localization.uae.vat": "construction_management_suite.localization.uae.overrides.get_vat_settings",
    },
}

# On app install
after_install = "construction_management_suite.setup.after_install"
after_migrate = "construction_management_suite.setup.after_migrate"
before_uninstall = "construction_management_suite.setup.before_uninstall"

# Portal menu items
portal_menu_items = []

# Notification config
notification_config = "construction_management_suite.utils.notifications.get_notification_config"

# Email digests
email_brand_image = "construction_management_suite/images/cms_logo.png"

# Website context
website_context = {}

# Override standard page
page_js = {}

# Buying straight off the estimate, with no forecast in between.
doctype_js = {
    "Material Request": ["public/js/material_request.js", "public/js/buying.js"],
    "Purchase Order": "public/js/buying.js",
    "Purchase Receipt": "public/js/buying.js",
    "Purchase Invoice": "public/js/buying.js",
    # For the two work pickers only — see the foot of buying.js.
    "Stock Entry": "public/js/buying.js",
}

# Permission query conditions — row-level security by company
permission_query_conditions = {
    "BOQ": "construction_management_suite.utils.permissions.get_company_filter",
    "Cost Estimation": "construction_management_suite.utils.permissions.get_company_filter",
    "Interim Payment Certificate": "construction_management_suite.utils.permissions.get_company_filter",
    "Daily Site Report": "construction_management_suite.utils.permissions.get_company_filter",
}

has_permission = {
    "BOQ": "construction_management_suite.utils.permissions.has_permission",
    "Interim Payment Certificate": "construction_management_suite.utils.permissions.has_permission",
}

# Standard controllers override
override_doctype_class = {}

# Show the construction documents in the Project's Connections tab.
# (`dashboards` is not a Frappe hook — nothing reads it; this is the real one.)
override_doctype_dashboards = {
    "Project": "construction_management_suite.overrides.project_dashboard.get_dashboard_data",
}
