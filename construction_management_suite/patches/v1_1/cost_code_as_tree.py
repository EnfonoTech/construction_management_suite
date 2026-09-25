"""Build the nested set behind cost codes that were entered as a flat list.

`Cost Code` shipped with a `parent_cost_code` link and no tree behind it, so
every existing record has the parent it was given and `lft`/`rgt` of zero.
Nothing rolls up until the set is numbered, and a tree doctype with an unbuilt
set answers every "everything under 01" query with nothing at all.

`rebuild_tree` reads the parent links and numbers the whole tree from them, so
the hierarchy the user already typed is what comes out.
"""

import frappe
from frappe.utils.nestedset import rebuild_tree


def execute():
    if not frappe.db.table_exists("Cost Code"):
        return
    frappe.reload_doc("project_costing", "doctype", "cost_code")

    # A leaf that was made the parent of something is the one thing rebuilding
    # cannot express — the tree would claim a code carries both its own budget
    # and its children's. It is a heading; say so.
    for name in frappe.db.sql_list(
        """SELECT DISTINCT p.name FROM `tabCost Code` p
           JOIN `tabCost Code` c ON c.parent_cost_code = p.name
           WHERE IFNULL(p.is_group, 0) = 0"""
    ):
        frappe.db.set_value("Cost Code", name, "is_group", 1, update_modified=False)

    rebuild_tree("Cost Code", "parent_cost_code")
