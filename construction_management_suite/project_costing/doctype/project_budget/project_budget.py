import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt
from construction_management_suite.utils.validations import validate_project_company
from construction_management_suite.utils.titles import (
    month_of,
    project_label,
    set_auto_title,
)


class ProjectBudget(Document):
    def before_cancel(self):
        # before, not on_cancel: on_cancel runs after the row is written.
        self.status = "Cancelled"

    def validate(self):
        set_auto_title(self, "budget_title", [_("Budget"), project_label(self.project), self.fiscal_year if self.get("fiscal_year") else None])
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
        actual is that account's GL entries for this project. Rows with no cost
        code keep whatever was entered manually.

        Where several rows lead back to one account — two cost codes posting to
        the same expense head, or one code used on two rows — the ledger cannot
        say which of them spent it, and giving each the whole account total
        reported the same money twice and three times over. It is apportioned by
        what each row was budgeted, which is the only signal there is, and the
        document says so rather than presenting an estimate as an observation.
        """
        coded = [i for i in self.items if i.cost_code]
        if not coded:
            return

        rows_by_account = {}
        for item in coded:
            account = frappe.db.get_value("Cost Code", item.cost_code, "debit_account")
            if account:
                rows_by_account.setdefault(account, []).append(item)
        if not rows_by_account:
            return

        actuals = frappe.db.sql(
            """
            SELECT account, SUM(debit - credit) AS actual
            FROM `tabGL Entry`
            WHERE project = %(project)s
              AND docstatus = 1
              AND is_cancelled = 0
              AND account IN %(accounts)s
            GROUP BY account
            """,
            {"project": self.project, "accounts": tuple(rows_by_account)},
            as_dict=True,
        )

        shared = []
        for row in actuals:
            members = rows_by_account.get(row.account) or []
            if len(members) == 1:
                members[0].actual_amount = flt(row.actual)
                continue
            base = sum(flt(m.budgeted_amount) for m in members)
            for member in members:
                share = flt(member.budgeted_amount) / base if base else 1.0 / len(members)
                member.actual_amount = flt(row.actual) * share
            shared.append((row.account, [m.idx for m in members]))

        if shared:
            frappe.msgprint(
                "<br>".join(
                    _("{0} — rows {1}").format(account, ", ".join(str(i) for i in idxs))
                    for account, idxs in shared
                )
                + _("<br><br>These rows post to the same account, so their actual "
                    "cost is apportioned by what each was budgeted. The ledger "
                    "cannot tell them apart; give them their own accounts to see "
                    "them separately."),
                title=_("Actual cost apportioned"),
                indicator="orange",
            )

    def calculate_variance(self):
        self.variance_amount = flt(self.total_budget) - flt(self.total_actual_cost)
        if flt(self.total_budget) > 0:
            self.budget_utilization_percent = flt(self.total_actual_cost) / flt(self.total_budget) * 100
        for item in self.items:
            item.variance = flt(item.budgeted_amount) - flt(item.actual_amount)

    def before_submit(self):
        self.status = "Active"

    @frappe.whitelist()
    def refresh_from_estimate(self):
        """Rewrite this budget from the project's current Cost Estimation.

        The other half of amending an estimate: submitting the amendment leaves
        a submitted budget alone, because it is an approved figure, so this is
        how the amended budget is brought onto the new plan without anybody
        retyping a cost plan.
        """
        from construction_management_suite.utils.validations import current_estimate

        if self.docstatus != 0:
            frappe.throw(
                _("Only a draft budget can be rebuilt. Amend this one first."),
                title=_("Budget is submitted"),
            )
        estimate = current_estimate(self.project)
        if not estimate:
            frappe.throw(
                _("{0} has no submitted Cost Estimation to build a budget from.")
                .format(self.project),
                title=_("Nothing to read"),
            )
        frappe.get_doc("Cost Estimation", estimate).fill_project_budget(self)
        self.save()
        frappe.msgprint(_("Rebuilt from {0}").format(estimate), alert=True)
        return estimate

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
