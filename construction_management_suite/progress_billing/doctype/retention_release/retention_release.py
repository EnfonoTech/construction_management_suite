import frappe
from frappe.model.document import Document
from construction_management_suite.utils.validations import validate_project_company


class RetentionRelease(Document):
    def validate(self):
        validate_project_company(self)
