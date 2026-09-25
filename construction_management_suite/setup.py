
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cint


def after_install():
    create_roles()
    create_missing_uoms()
    create_custom_fields_on_erpnext()
    create_billing_items()
    seed_check_defaults()
    frappe.db.commit()


def after_migrate():
    create_missing_uoms()
    create_custom_fields_on_erpnext()
    create_billing_items()
    seed_check_defaults()
    frappe.db.commit()


def seed_check_defaults():
    """Write the docfield default of any Check in the settings nobody has stored.

    A Single is loaded out of `tabSingles`, and a field with no row there comes
    back cast to 0 — so a checkbox whose docfield default is 1 reads as *off*
    to every piece of server code, while the form shows it ticked. A Select can
    live with that, because blank means "use the module default" and the code
    says what that is; a Check has no blank state, so the default has to be
    materialised.

    Written once, when the row is missing, and never touched again: a site that
    unticks a box stores a 0, and a 0 is a row.
    """
    settings = "Construction Settings"
    stored = set(
        frappe.db.sql_list("SELECT field FROM tabSingles WHERE doctype = %s", settings)
    )
    for df in frappe.get_meta(settings).fields:
        if df.fieldtype != "Check" or df.fieldname in stored:
            continue
        frappe.db.set_single_value(settings, df.fieldname, cint(df.default))
    frappe.clear_document_cache(settings, settings)


def create_missing_uoms():
    """Units the trade writes on a bill that ERPNext does not ship.

    A bill priced in bags, rolls and trips cannot be imported against a UOM
    list that has none of them, and inventing an approximation silently
    changes what was ordered.
    """
    for uom in ("Bag", "Roll", "Month", "Ls", "Trip", "Drum", "Qtn", "Ctn", "Pkt", "RM"):
        if not frappe.db.exists("UOM", uom):
            frappe.get_doc({"doctype": "UOM", "uom_name": uom}).insert(ignore_permissions=True)


def create_billing_items():
    """Service items the generated invoices are written against.

    Idempotent, and it never overwrites a setting a site has already pointed at
    its own item — so an upgrade adds what is missing and leaves choices alone.
    """
    from construction_management_suite.utils.billing import create_service_items

    try:
        created = create_service_items()
        if created:
            print(f"Construction: created billing items {', '.join(created)}")
    except Exception:
        # A fresh site may not have Item Groups or UOMs yet; the settings can be
        # filled in by hand, so this must never abort an install or a migrate.
        frappe.log_error(frappe.get_traceback(), "Construction: billing item setup failed")


def before_uninstall():
    remove_custom_fields()
    frappe.db.commit()


def create_roles():
    roles = [
        "Construction Admin",
        "Construction Project Manager",
        "Construction Site Engineer",
        "Construction Quantity Surveyor",
        "Construction Subcontractor",
        "Construction Billing Officer",
        "Construction Viewer",
    ]
    for role in roles:
        if not frappe.db.exists("Role", role):
            frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(
                ignore_permissions=True
            )


def _work_ref_fields(after):
    """The work reference, as it appears on every buying document.

    One Link to the work Item, not the old hidden-row-name plus visible-number
    pair. The Item is the key: it survives a bill being revised, where a child
    row name does not.

    Left editable on purpose: a buyer raising an order straight off the estimate
    has to be able to say which work it is for, and a wrong one has to be
    correctable.

    Two Item links now sit on the same row — `item_code` is the cement,
    `cms_work_item` is the painting it is for. Hence the explicit label; the
    list is narrowed in `public/js/buying.js`, not here.

    **No `link_filters`.** It used to carry one, restricting the picker to
    service items, and it made a custom query impossible: frappe's
    `apply_link_field_filters` calls the field's `get_query` with no arguments,
    keeps only its `filters`, throws away its `query` and replaces the function
    with a plain filter. So the project-scoped query was silently discarded and
    `project` went to the standard Item search, which answered "Unknown column
    tabItem.project". The query now supplies both the scope and the service-item
    restriction itself.
    """
    return [
        {
            "fieldname": "cms_work_item",
            "label": "For Work",
            "fieldtype": "Link",
            "options": "Item",
            # No link_filters, deliberately — see above. It is a JSON column
            # with a check constraint, so it cannot be blanked by passing an
            # empty string either; the patch sets it to NULL.
            "insert_after": after,
        },
    ]


def create_custom_fields_on_erpnext():
    """Add CMS fields to ERPNext standard doctypes for seamless integration."""
    custom_fields = {
        "Project": [
            {
                "fieldname": "cms_project_type",
                "label": "Project Type",
                "fieldtype": "Select",
                "options": "\nBuilding Construction\nCivil Works\nMEP\nInfrastructure\nInterior Fit-Out\nRoad Works\nOil & Gas\nOther",
                "insert_after": "project_type",
            },
            {
                "fieldname": "cms_contract_value",
                "label": "Contract Value",
                "fieldtype": "Currency",
                "insert_after": "cms_project_type",
            },
            {
                "fieldname": "cms_client_po",
                "label": "Client PO / Contract No",
                "fieldtype": "Data",
                "insert_after": "cms_contract_value",
            },
            {
                "fieldname": "cms_advance_amount",
                "label": "Advance Received",
                "fieldtype": "Currency",
                "description": "Paid by the client up front, recovered from certificates",
                "insert_after": "cms_client_po",
            },
            {
                "fieldname": "cms_retention_percent",
                "label": "Retention %",
                "fieldtype": "Percent",
                "description": "Blank = the module default",
                "insert_after": "cms_client_po",
            },
            # A job has a site store the way it has a cost centre, and every
            # material document on it asks the same question. Left blank on a
            # project that runs several stores, so the entry still has to say
            # which one — the default is a convenience, never an assumption.
            {
                "fieldname": "cms_default_warehouse",
                "label": "Default Warehouse",
                "fieldtype": "Link",
                "options": "Warehouse",
                "description": "Blank if the job runs more than one store",
                "insert_after": "cost_center",
            },
        ],
        "Purchase Order": [
            {
                "fieldname": "cms_subcontract_ref",
                "label": "Subcontract Ref",
                "fieldtype": "Link",
                "options": "Subcontract Agreement",
                "insert_after": "title",
            },
        ],
        "Sales Invoice": [
            {
                "fieldname": "cms_ipc_ref",
                "label": "IPC Reference",
                "fieldtype": "Link",
                "options": "Interim Payment Certificate",
                "insert_after": "title",
            },
            {
                "fieldname": "cms_retention_release_ref",
                "label": "Retention Release Ref",
                "fieldtype": "Link",
                "options": "Retention Release",
                "insert_after": "cms_ipc_ref",
            },
        ],
        "Purchase Invoice": [
            {
                "fieldname": "cms_subcontract_certificate_ref",
                "label": "Subcontractor Certificate Ref",
                "fieldtype": "Link",
                "options": "Subcontractor Payment Certificate",
                "insert_after": "title",
            },
        ],
        # The line of work a purchase is for. Named the same on every buying
        # document on purpose: frappe's mapper copies same-named fields, so the
        # reference rides from request to order to receipt to invoice with no
        # mapping code. Never no_copy, or it would stop at the first hop.
        "Material Request Item": _work_ref_fields("item_code"),
        "Purchase Order Item": _work_ref_fields("item_code"),
        "Purchase Receipt Item": _work_ref_fields("item_code"),
        "Purchase Invoice Item": _work_ref_fields("item_code"),
        # The stock entries a transfer or a consumption entry posts, so the
        # movement itself says which work it was for.
        "Stock Entry Detail": _work_ref_fields("item_code"),
        "Material Request": [
            {
                "fieldname": "cms_forecast_ref",
                "label": "Material Forecast Ref",
                "fieldtype": "Link",
                "options": "Material Forecast",
                "insert_after": "title",
            },
            {
                "fieldname": "cms_site_request_ref",
                "label": "Site Material Request Ref",
                "fieldtype": "Link",
                "options": "Site Material Request",
                "insert_after": "title",
            },
        ],
        "Stock Entry": [
            {
                "fieldname": "cms_site_ref",
                "label": "Site Transfer Ref",
                "fieldtype": "Link",
                "options": "Site Transfer",
                "insert_after": "title",
            },
            {
                "fieldname": "cms_consumption_ref",
                "label": "Material Consumption Ref",
                "fieldtype": "Link",
                "options": "Material Consumption Entry",
                "insert_after": "cms_site_ref",
            },
        ],
    }
    create_custom_fields(custom_fields, ignore_validate=True)


# Every field this app creates on an ERPNext doctype, by name. Deleting on
# `fieldname like 'cms_%'` alone would take another app's cms_ fields with it.
_OWN_CUSTOM_FIELDS = (
    "cms_project_type", "cms_contract_value", "cms_client_po", "cms_advance_amount",
    "cms_retention_percent", "cms_subcontract_ref", "cms_ipc_ref",
    "cms_retention_release_ref", "cms_subcontract_certificate_ref",
    "cms_forecast_ref", "cms_site_request_ref", "cms_site_ref",
    "cms_consumption_ref", "cms_work_item", "cms_default_warehouse",
)


def remove_custom_fields():
    frappe.db.delete("Custom Field", {"fieldname": ["in", _OWN_CUSTOM_FIELDS]})
