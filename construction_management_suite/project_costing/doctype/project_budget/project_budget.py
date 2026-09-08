import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt
from construction_management_suite.utils.validations import validate_project_company


class ProjectBudget(Document):
    def validate(self):
        validate_project_company(self)
        self.fetch_actual_costs()
        self.fetch_committed_costs()
        self.calculate_variance()

    def fetch_actual_costs(self):
        """Sum expense-account GL entries tagged to this project."""
        actual = (
            frappe.db.sql(
                """
                SELECT SUM(gle.debit - gle.credit) AS actual
                FROM `tabGL Entry` gle
                JOIN `tabAccount` acc ON acc.name = gle.account
                WHERE gle.project = %s
                  AND gle.docstatus = 1
                  AND gle.is_cancelled = 0
                  AND acc.root_type = 'Expense'
                """,
                self.project,
                as_dict=True,
            )[0].get("actual")
            or 0
        )
        self.total_actual_cost = flt(actual)
        self._distribute_actuals_to_items()

    def fetch_committed_costs(self):
        """Sum outstanding Purchase Orders for this project."""
        committed = (
            frappe.db.sql(
                """
                SELECT SUM(grand_total - advance_paid) AS committed
                FROM `tabPurchase Order`
                WHERE project = %s AND docstatus = 1 AND status NOT IN ('Completed','Cancelled')
                """,
                self.project,
                as_dict=True,
            )[0].get("committed")
            or 0
        )
        self.total_committed_cost = flt(committed)

    def _distribute_actuals_to_items(self):
        """Break the project's actual spend down onto rows that name a Cost Code.

        A Cost Code carries the expense account its spend lands in, so each row's
        actual is the sum of that account's GL entries for this project. Rows with
        no cost code keep whatever was entered manually.
        """
        coded = [i for i in self.items if i.cost_code]
        if not coded:
            return

        accounts = {}
        for code in {i.cost_code for i in coded}:
            account = frappe.db.get_value("Cost Code", code, "debit_account")
            if account:
                accounts.setdefault(account, []).append(code)
        if not accounts:
            return

        rows = frappe.db.sql(
            """
            SELECT account, SUM(debit - credit) AS actual
            FROM `tabGL Entry`
            WHERE project = %(project)s
              AND docstatus = 1
              AND is_cancelled = 0
              AND account IN %(accounts)s
            GROUP BY account
            """,
            {"project": self.project, "accounts": tuple(accounts)},
            as_dict=True,
        )
        by_code = {}
        for row in rows:
            for code in accounts[row.account]:
                by_code[code] = flt(by_code.get(code)) + flt(row.actual)

        for item in coded:
            if item.cost_code in by_code:
                item.actual_amount = flt(by_code[item.cost_code])

    def calculate_variance(self):
        self.variance_amount = flt(self.total_budget) - flt(self.total_actual_cost)
        if flt(self.total_budget) > 0:
            self.budget_utilization_percent = flt(self.total_actual_cost) / flt(self.total_budget) * 100
        for item in self.items:
            item.variance = flt(item.budgeted_amount) - flt(item.actual_amount)

    def before_submit(self):
        self.status = "Active"

    @frappe.whitelist()
    def refresh_actuals(self):
        self.fetch_actual_costs()
        self.fetch_committed_costs()
        self.calculate_variance()
        if self.docstatus == 1:
            # A submitted budget cannot be save()d, but refreshing it is the
            # whole point of the button — write the figures straight through.
            self.db_update()
            for item in self.items:
                item.db_update()
        else:
            self.save()
        frappe.msgprint(_("Actuals refreshed"))
