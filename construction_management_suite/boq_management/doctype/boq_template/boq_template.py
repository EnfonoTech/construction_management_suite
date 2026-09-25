import frappe
from frappe.model.document import Document

from construction_management_suite.utils.validations import validate_one_row_per_work_item


class BOQTemplate(Document):
    def validate(self):
        # A template is a bill waiting to be copied, so it has to obey the rule
        # the bill does — otherwise the same work lands on a BOQ twice and the
        # refusal comes at the far end, on a document nobody typed by hand.
        validate_one_row_per_work_item(self.items)
