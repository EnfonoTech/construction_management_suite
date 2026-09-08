"""
Row-level permission helpers — multi-company isolation.
"""

import frappe


def get_company_filter(user, doctype=None):
    """Restrict list queries to companies the user is allowed to access.

    Frappe passes the doctype as the second argument, so the same helper can be
    registered for every CMS doctype that carries a `company` field.
    """
    if "CMS Admin" in frappe.get_roles(user):
        return ""

    if not doctype:
        return ""

    companies = frappe.db.sql_list(
        """
        SELECT for_value FROM `tabUser Permission`
        WHERE user = %s AND allow = 'Company'
        """,
        user,
    )
    if not companies:
        # Fall back to default company
        default = frappe.defaults.get_user_default("company", user)
        companies = [default] if default else []
    if not companies:
        return "1=0"  # No access

    values = ", ".join(frappe.db.escape(c) for c in companies)
    return f"`tab{doctype}`.company in ({values})"


def has_permission(doc, user=None, permission_type=None):
    """
    Extra permission gate: subcontractors may only see their own documents.
    """
    user = user or frappe.session.user
    roles = frappe.get_roles(user)

    # Admins always pass
    if "CMS Admin" in roles or "System Manager" in roles:
        return True

    # Subcontractor portal users: only their linked supplier records
    if "CMS Subcontractor" in roles and doc.doctype == "Subcontractor Payment Certificate":
        supplier = frappe.db.get_value("Supplier", {"email_id": user}, "name")
        return doc.subcontractor == supplier

    return True
