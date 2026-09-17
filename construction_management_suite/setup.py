import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def after_install():
    create_roles()
    create_custom_fields_on_erpnext()
    frappe.db.commit()


def after_migrate():
    create_custom_fields_on_erpnext()
    frappe.db.commit()


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
                "fieldname": "cms_retention_percent",
                "label": "Retention %",
                "fieldtype": "Percent",
                "default": "10",
                "insert_after": "cms_client_po",
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
        ],
        "Stock Entry": [
            {
                "fieldname": "cms_site_ref",
                "label": "Site Transfer Ref",
                "fieldtype": "Link",
                "options": "Site Transfer",
                "insert_after": "title",
            },
        ],
    }
    create_custom_fields(custom_fields, ignore_validate=True)


def remove_custom_fields():
    frappe.db.delete("Custom Field", {"fieldname": ["like", "cms_%"]})
