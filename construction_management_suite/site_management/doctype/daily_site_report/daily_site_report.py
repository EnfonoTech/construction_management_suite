import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, flt, nowdate
from construction_management_suite.utils.validations import validate_project_company


class DailySiteReport(Document):
    def validate(self):
        self.set_activity_progress()
        validate_project_company(self)
        self.validate_date()
        self.validate_one_per_day()
        self.set_submitted_by()
        self.calculate_labour_cost()
        self.calculate_equipment_cost()

    def validate_date(self):
        # getdate on both sides: a field read back from the database is a
        # date object while a field typed in the form is a string, and Python
        # refuses to compare the two.
        if self.report_date and getdate(self.report_date) > getdate(nowdate()):
            frappe.throw(_("Report date cannot be in the future"))

    def set_submitted_by(self):
        if not self.submitted_by:
            self.submitted_by = frappe.session.user

    def validate_one_per_day(self):
        """A site diary is one entry per day — a second one for the same date
        splits the day's record in two and makes the labour totals wrong."""
        if not (self.project and self.report_date):
            return
        existing = frappe.db.get_value(
            "Daily Site Report",
            {
                "project": self.project,
                "report_date": self.report_date,
                "docstatus": ["<", 2],
                "name": ["!=", self.name or ""],
            },
            "name",
        )
        if existing:
            frappe.throw(
                _("{0} already covers {1} on this project").format(
                    frappe.utils.get_link_to_form("Daily Site Report", existing),
                    frappe.utils.formatdate(self.report_date),
                ),
                title=_("Report Already Filed"),
            )

    def calculate_labour_cost(self):
        """Overtime is worked and paid, so it belongs in the day's labour cost."""
        for row in self.labour:
            row.daily_cost = (
                flt(row.headcount) * flt(row.daily_rate)
                + flt(row.overtime_hours) * flt(row.overtime_rate)
            )

    def calculate_equipment_cost(self):
        """Idle plant still costs — it is on hire whether it turns or not."""
        for row in self.equipment:
            row.cost = (flt(row.hours_worked) + flt(row.idle_hours)) * flt(row.hourly_rate)

    @frappe.whitelist()
    def get_activities_from_works(self):
        """List the priced work of this project, for the site to report against.

        The activity table already had a place for the work reference and it was
        filled on no row, so progress was a sentence in a diary and nothing
        could be measured against the plan. The list comes from whichever
        document prices the job, so the site reports against the same lines the
        materials were planned from.
        """
        from construction_management_suite.material_planning.doctype.material_consumption_entry.material_consumption_entry import (
            work_lines,
        )

        if not self.project:
            frappe.throw(_("Choose the project this report is for"))
        on_form = {a.boq_item_ref for a in self.activities if a.boq_item_ref}
        added = 0
        for line in work_lines(self.project):
            if line.ref in on_form:
                continue
            self.append("activities", {
                "activity_description": (line.description or line.item_code or "")[:140],
                "boq_item_ref": line.ref,
                "boq_item_no": line.line_no or line.item_code,
                "uom": line.uom,
            })
            added += 1
        self.set_activity_progress()
        return added

    def set_activity_progress(self):
        """How far each line of work has got, counting every report before this.

        Per row this is cumulative, not today's share: a foreman reporting 40 m2
        of plaster wants to know the wall is 62% done, not that today was 4%.
        """
        if not self.project:
            return
        refs = [a.boq_item_ref for a in self.activities if a.boq_item_ref]
        if not refs:
            return

        done, total = {}, {}
        for row in frappe.db.sql(
            """
            SELECT a.boq_item_ref AS ref, SUM(a.actual_qty) AS qty
            FROM `tabSite Report Activity` a
            JOIN `tabDaily Site Report` d ON d.name = a.parent
            WHERE d.project = %(p)s AND d.docstatus = 1 AND d.name != %(n)s
              AND a.boq_item_ref IN %(refs)s
            GROUP BY a.boq_item_ref
            """,
            {"p": self.project, "n": self.name or "", "refs": refs},
            as_dict=True,
        ):
            done[row.ref] = flt(row.qty)

        source = "Cost Estimation Item" if frappe.db.exists(
            "Cost Estimation", {"project": self.project, "docstatus": 1}) else "BOQ Item"
        for ref in refs:
            total[ref] = flt(frappe.db.get_value(source, ref, "qty"))

        for activity in self.activities:
            ref = activity.boq_item_ref
            if not ref or not flt(total.get(ref)):
                continue
            cumulative = flt(done.get(ref)) + flt(activity.actual_qty)
            activity.percent_complete = cumulative / flt(total[ref]) * 100

    def before_submit(self):
        self.status = "Submitted"

    def before_cancel(self):
        # before, not on_cancel: on_cancel runs after the row is written.
        self.status = "Cancelled"

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
