import frappe


def get_cost_center(project=None, company=None):
    """The cost centre a project's postings should land in.

    Contractors normally open one cost centre per project so that job costs and
    revenue stay separated across concurrent jobs. Use the project's own if it
    has one; fall back to the company default for projects that do not.
    """
    if project:
        cost_center = frappe.db.get_value("Project", project, "cost_center")
        if cost_center:
            return cost_center
    if company:
        return frappe.get_cached_value("Company", company, "cost_center")
    return None
