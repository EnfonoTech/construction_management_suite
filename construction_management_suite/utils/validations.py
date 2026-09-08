import frappe
from frappe import _


def validate_project_company(doc, project_fields=("project",)):
    """A document must belong to the same company as the project it is against.

    On a site running several companies, attaching a certificate or a budget to
    another company's project silently posts to the wrong books, so this is
    enforced on the server rather than left to the form's link filter.
    """
    if not doc.get("company"):
        return
    for fieldname in project_fields:
        project = doc.get(fieldname)
        if not project:
            continue
        project_company = frappe.db.get_value("Project", project, "company")
        if project_company and project_company != doc.company:
            frappe.throw(
                _("{0} {1} belongs to company {2}, but this document is for {3}").format(
                    _(doc.meta.get_label(fieldname)), project, project_company, doc.company
                ),
                title=_("Company Mismatch"),
            )
