"""One spreadsheet in, a whole cost plan out — as drafts, with a record of it.

A document rather than a button on the settings page, because an import is an
event: which file, into which project, on what day, and what it made. A Single
holds one attachment and forgets the last one, so nobody could answer "where
did these 300 items come from" a month later. This can.

What one file creates:

    work Items          one per line of work
    resource Items      one per distinct resource description
    Rate Analysis       one per line of work, Draft
    Cost Estimation     one, with a line per work, Draft

Nothing is submitted. Read it, fix what the spreadsheet got wrong, submit it.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class EstimateImport(Document):
    def validate(self):
        if self.project and self.company:
            on = frappe.db.get_value("Project", self.project, "company")
            if on and on != self.company:
                frappe.throw(
                    _("{0} belongs to {1}, not {2}").format(self.project, on, self.company)
                )
        if not self.status:
            self.status = "Draft"

    def on_trash(self):
        """Deleting the record does not undo the import.

        The Items, analyses and estimate it made are ordinary documents with
        their own lives by then — one of them may already be submitted. Say so
        rather than letting somebody believe the delete cleaned up.
        """
        if self.cost_estimation and frappe.db.exists("Cost Estimation", self.cost_estimation):
            frappe.msgprint(
                _("{0} and everything this import created stay where they are. "
                  "Deleting this record only removes the note of how they got there.")
                .format(self.cost_estimation),
                indicator="orange",
            )

    # ── the two buttons ───────────────────────────────────────────────────

    @frappe.whitelist()
    def check_file(self):
        """Read the file, report what it says, write nothing."""
        from construction_management_suite.importers.estimate_import import run

        result = run(file_url=self.import_file, project=self.project,
                     company=self.company, dry=1)
        self.db_set({
            "status": "Checked",
            "works_count": result["works"],
            "resources_count": result["rows"],
            "items_created": result["new_items"],
            "log": frappe.as_json(result),
        }, update_modified=False)
        return result

    @frappe.whitelist()
    def import_now(self):
        """Build it. Refuses to run twice from the same record."""
        from construction_management_suite.importers.estimate_import import run

        if self.cost_estimation:
            frappe.throw(
                _("This import already produced {0}. Raise a new Estimate Import "
                  "to bring in another file.").format(self.cost_estimation)
            )

        try:
            result = run(file_url=self.import_file, project=self.project,
                         company=self.company, dry=0)
        except Exception:
            self.db_set("status", "Failed", update_modified=False)
            raise

        self.db_set({
            "status": "Imported",
            "cost_estimation": result["estimate"],
            "works_count": result["works"],
            "resources_count": result["rows"],
            "items_created": result["new_items"],
            "log": frappe.as_json(result),
        }, update_modified=False)
        frappe.msgprint(
            _("Cost Estimation {0} created as a draft, with {1} rate analyses. "
              "Read it before submitting.").format(
                  frappe.utils.get_link_to_form("Cost Estimation", result["estimate"]),
                  result["works"]),
            title=_("Imported"),
            indicator="green",
        )
        return result

    @frappe.whitelist()
    def stored_result(self):
        """What the last check or import found, for the form to draw."""
        if not self.log:
            return None
        try:
            return frappe.parse_json(self.log)
        except (ValueError, TypeError):
            return None
