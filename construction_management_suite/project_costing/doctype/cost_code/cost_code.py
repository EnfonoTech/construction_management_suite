"""The job's cost breakdown, as a tree rather than a list of strings.

A cost code is only useful because it nests: 01 is the substructure, 01-100 the
earthworks under it, and the question a project manager asks is "what has the
substructure cost", not "what has 01-100 cost". The doctype carried a
`parent_cost_code` link and an `is_group` checkbox from the start, so it looked
like a tree and read like one — but it was not `is_tree`, there was no nested
set behind it, and nothing ever rolled a child's spend up to its parent. Every
report showed the leaves side by side with their own parents and the two never
added up.

It is a `NestedSet` now, which is what makes "everything under 01" a range
query rather than a recursive walk, and what lets the desk show it as a tree.
The rules that go with a tree are enforced here: a parent has to be a group, a
group with children cannot be turned back into a leaf, and a child belongs to
the same company as its parent.
"""

import frappe
from frappe import _
from frappe.utils.nestedset import NestedSet


class CostCode(NestedSet):
    nsm_parent_field = "parent_cost_code"

    def validate(self):
        self.validate_parent()
        self.validate_company()
        self.validate_group_change()

    def validate_parent(self):
        """A code may only hang off a group, and never off itself."""
        if not self.parent_cost_code:
            return
        if self.parent_cost_code == self.name:
            frappe.throw(_("A cost code cannot be its own parent"))
        if not frappe.db.get_value("Cost Code", self.parent_cost_code, "is_group"):
            frappe.throw(
                _(
                    "{0} is not a group, so nothing can sit under it.<br><br>Tick "
                    "<b>Is Group</b> on it first — a group is a heading that carries "
                    "no budget of its own and totals what is beneath it."
                ).format(self.parent_cost_code),
                title=_("The parent must be a group"),
            )

    def validate_company(self):
        """A child under another company's heading would roll up into its totals."""
        if not (self.parent_cost_code and self.company):
            return
        parent_company = frappe.db.get_value("Cost Code", self.parent_cost_code, "company")
        if parent_company and parent_company != self.company:
            frappe.throw(
                _("{0} belongs to {1}, and this code belongs to {2}.").format(
                    self.parent_cost_code, parent_company, self.company
                ),
                title=_("Parent belongs to another company"),
            )

    def validate_group_change(self):
        """A heading with codes under it cannot become a leaf.

        The children would be orphaned mid-tree, and the budget lines under
        them would stop rolling up to anything.
        """
        if self.is_new() or self.is_group:
            return
        if not frappe.db.get_value("Cost Code", self.name, "is_group"):
            return
        children = frappe.db.count("Cost Code", {"parent_cost_code": self.name})
        if children:
            frappe.throw(
                _(
                    "{0} has {1} cost code(s) under it, so it has to stay a group. "
                    "Move them elsewhere first."
                ).format(self.name, children),
                title=_("This code has children"),
            )


@frappe.whitelist()
def get_children(doctype=None, parent=None, company=None, is_root=False):
    """The tree view's own reader: one level at a time, scoped by company."""
    # A chart legitimately has several roots: one heading per section, plus
    # standalone leaves like Preliminaries that sit under nothing.
    if is_root or not parent or parent == "All Cost Codes":
        filters = {"parent_cost_code": ("in", (None, ""))}
    else:
        filters = {"parent_cost_code": parent}
    if company:
        filters["company"] = company

    return frappe.get_all(
        "Cost Code",
        fields=["name as value", "cost_code_name as title", "is_group as expandable",
                "cost_category", "disabled"],
        filters=filters,
        order_by="name",
    )


@frappe.whitelist()
def add_node():
    """Create from the tree view's "New" button, with the parent pre-filled.

    The four lines `frappe.desk.treeview.make_tree_args` would do are done
    here instead. This app is meant to run on frappe 15 generally, and a desk
    helper is a moving target — one such import already broke a form on a
    bench two patch releases behind this one.
    """
    args = frappe._dict(frappe.form_dict)
    args.pop("cmd", None)
    args.pop("is_root", None)

    parent = args.get("parent") or args.get("parent_cost_code")
    # The tree's synthetic root is a label, not a record.
    args.parent_cost_code = None if parent == "All Cost Codes" else parent
    args.doctype = "Cost Code"
    frappe.get_doc(args).insert()
