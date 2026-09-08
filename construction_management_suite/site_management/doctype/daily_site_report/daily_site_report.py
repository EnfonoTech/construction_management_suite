import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate
from construction_management_suite.utils.validations import validate_project_company


class DailySiteReport(Document):
    def validate(self):
        validate_project_company(self)
        self.validate_date()
        self.set_submitted_by()
        self.calculate_labour_cost()

    def validate_date(self):
        if self.report_date and self.report_date > nowdate():
            frappe.throw(_("Report date cannot be in the future"))

    def set_submitted_by(self):
        if not self.submitted_by:
            self.submitted_by = frappe.session.user

    def calculate_labour_cost(self):
        for row in self.labour:
            row.daily_cost = flt(row.headcount) * flt(row.daily_rate)

    def before_submit(self):
        self.status = "Submitted"

    def on_submit(self):
        self._update_project_progress()

    def _update_project_progress(self):
        """Update ERPNext Project percent_complete from the most recent report."""
        if not (self.project and self.cumulative_percent_complete):
            return

        latest = frappe.db.get_value(
            "Daily Site Report",
            {
                "project": self.project,
                "docstatus": 1,
                "name": ["!=", self.name],
                "report_date": [">", self.report_date],
            },
            "name",
        )
        if latest:
            # A later report already set progress — a backdated one must not undo it.
            return

        # ERPNext owns `percent_complete` and recomputes it from Tasks on every
        # Project.save() — which it does whenever an invoice, stock entry or
        # timesheet is submitted against the project. Unless the project is set to
        # "Manual", site-reported progress is silently wiped minutes after we write it.
        frappe.db.set_value(
            "Project",
            self.project,
            {
                "percent_complete_method": "Manual",
                "percent_complete": flt(self.cumulative_percent_complete),
            },
        )
