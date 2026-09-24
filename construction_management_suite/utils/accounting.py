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


def get_warehouse(project=None, company=None):
    """The store a project's material moves through.

    The same shape as `get_cost_center`, and for the same reason: a job has one
    site store as surely as it has one cost centre, and every material document
    on it was asking the question again. Blank on the project means the job runs
    more than one store — then there is no right answer to fill in, and the
    entry has to say which.

    Never used to overwrite a warehouse somebody chose; only to fill a blank.
    """
    if project:
        warehouse = frappe.db.get_value("Project", project, "cms_default_warehouse")
        # Checked rather than trusted: the picker filters by company, but a
        # project imported or edited before it did could name another company's
        # store, and filling that onto a document only moves the refusal to the
        # far end where it makes no sense.
        if warehouse and (
            not company or frappe.db.get_value("Warehouse", warehouse, "company") == company
        ):
            return warehouse
    if company:
        # Stock Settings is the only site-wide default ERPNext keeps, and it is
        # not per company — so it is used only when it belongs to this one.
        default = frappe.db.get_single_value("Stock Settings", "default_warehouse")
        if default and frappe.db.get_value("Warehouse", default, "company") == company:
            return default
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
