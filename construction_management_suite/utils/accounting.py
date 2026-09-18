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


def consumption_account(company):
    """Where material issued to a site is charged.

    Left to ERPNext, a Material Issue posts to Stock Adjustment — an inventory
    variance account. Material consumed on a job is not a variance, it is the
    cost of the work, and a project's actual cost read from an adjustment
    account is a coincidence rather than a figure anyone chose.
    """
    from construction_management_suite.utils.settings import cms_setting

    account = cms_setting("consumption_expense_account")
    if account and frappe.db.get_value("Account", account, "company") == company:
        return account
    return frappe.get_cached_value("Company", company, "default_expense_account") or None
