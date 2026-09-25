"""Budget against actual and committed, down the cost breakdown.

The report used to list every budget line side by side, which on a chart that
nests reads as nonsense: 01 Substructure and 01-100 Earthworks appear as peers,
the first showing nothing because a heading carries no budget of its own, and
nobody can answer "what has the substructure cost". `Cost Code` is a real tree
now, so this walks it — a group shows what everything beneath it adds up to,
and the leaves keep their own figures.

Two rules make the totals honest:

* a group's own budget lines are counted once, with its children's, and never
  twice — a heading should not carry lines, but one that does is not silently
  dropped;
* budget lines with no cost code at all are gathered under one row at the foot
  rather than scattered through the tree, because they belong nowhere in it and
  hiding them would make the report disagree with the budget it came from.
"""

import frappe
from frappe import _
from frappe.utils import flt

NOT_CODED = "__uncoded__"


def execute(filters=None):
    filters = filters or {}
    data = get_data(filters)
    return get_columns(), data, _unattributed(filters, data), get_chart(data)


def _unattributed(filters, data):
    """Say when the spend exists but no cost code can claim it.

    A row's actual is the GL total of the expense account its Cost Code points
    at. A code with no `debit_account` therefore reads zero however much the
    project spent, and the report showed a budget fully unspent next to a
    header saying otherwise — the one failure mode where silence is worse than
    a wrong number.
    """
    if not data:
        return None
    spent = flt(frappe.db.sql(
        """SELECT SUM(total_actual_cost) FROM `tabProject Budget`
           WHERE docstatus = 1 {where}""".format(
            where="".join(
                f" AND {field} = %({field})s" for field in ("project", "company")
                if filters.get(field)
            )
        ),
        {k: v for k, v in filters.items() if k in ("project", "company")},
    )[0][0])
    attributed = sum(flt(r["actual_amount"]) for r in data if not r["is_group"])
    if spent - attributed <= 0.005:
        return None

    unmapped = frappe.get_all(
        "Cost Code",
        filters={"debit_account": ("in", (None, "")), "is_group": 0,
                 **({"company": filters["company"]} if filters.get("company") else {})},
        pluck="name",
    )
    note = _("{0} of spend on {1} is not on any line below. A row's actual is "
             "read from the expense account its Cost Code points at.").format(
        frappe.utils.fmt_money(spent - attributed),
        filters.get("project") or _("these budgets"),
    )
    if unmapped:
        note += _(
            "<br>These cost codes have no Debit Account, so nothing can be "
            "attributed to them: {0}"
        ).format(", ".join(unmapped[:12]))
    return note


def get_columns():
    return [
        {"label": _("Cost Code"), "fieldname": "cost_head", "fieldtype": "Data", "width": 260},
        {"label": _("Category"), "fieldname": "cost_category", "fieldtype": "Data", "width": 110},
        {"label": _("Budgeted"), "fieldname": "budgeted_amount", "fieldtype": "Currency", "width": 140},
        {"label": _("Actual"), "fieldname": "actual_amount", "fieldtype": "Currency", "width": 140},
        {"label": _("Committed"), "fieldname": "committed_amount", "fieldtype": "Currency", "width": 140},
        {"label": _("Variance"), "fieldname": "variance", "fieldtype": "Currency", "width": 140},
        {"label": _("Utilization %"), "fieldname": "utilization_pct", "fieldtype": "Percent", "width": 110},
        {"label": _("Project"), "fieldname": "project", "fieldtype": "Link", "options": "Project", "width": 140},
    ]


def get_data(filters):
    lines = _budget_lines(filters)
    if not lines:
        return []

    own = {}
    for row in lines:
        key = row.cost_code or NOT_CODED
        entry = own.setdefault(key, {
            "budgeted_amount": 0.0, "actual_amount": 0.0, "committed_amount": 0.0,
            "projects": set(), "category": row.cost_category,
        })
        entry["budgeted_amount"] += flt(row.budgeted_amount)
        entry["actual_amount"] += flt(row.actual_amount)
        entry["committed_amount"] += flt(row.committed_amount)
        entry["projects"].add(row.project)

    data = []
    for root in _tree(filters):
        _walk(root, own, data, 0)
    if NOT_CODED in own:
        data.append(_row(NOT_CODED, _("Not coded"), None, own[NOT_CODED], 0, is_group=False))
    return data


def _walk(node, own, data, indent):
    """Depth first, parent before children — the order a tree report needs."""
    row = _row(
        node["name"],
        f"{node['name']} — {node['cost_code_name']}" if node["cost_code_name"] else node["name"],
        node["parent"],
        own.get(node["name"]),
        indent,
        is_group=bool(node["is_group"]),
        category=node["cost_category"],
    )
    data.append(row)

    at = len(data)
    for child in node["children"]:
        _walk(child, own, data, indent + 1)

    # Roll what the children came to into this row, after they are laid out.
    for child_row in data[at:]:
        if flt(child_row["indent"]) != indent + 1:
            continue
        for field in ("budgeted_amount", "actual_amount", "committed_amount"):
            row[field] += flt(child_row[field])
    _finish(row)


def _row(name, label, parent, totals, indent, is_group=False, category=None):
    totals = totals or {}
    row = {
        "cost_code": name,
        "cost_head": label,
        "parent_cost_code": parent,
        "indent": indent,
        "is_group": 1 if is_group else 0,
        "cost_category": category or totals.get("category"),
        "budgeted_amount": flt(totals.get("budgeted_amount")),
        "actual_amount": flt(totals.get("actual_amount")),
        "committed_amount": flt(totals.get("committed_amount")),
        "project": None,
    }
    projects = totals.get("projects") or set()
    if len(projects) == 1:
        row["project"] = next(iter(projects))
    _finish(row)
    return row


def _finish(row):
    row["variance"] = flt(row["budgeted_amount"]) - flt(row["actual_amount"])
    row["utilization_pct"] = (
        flt(row["actual_amount"]) / flt(row["budgeted_amount"]) * 100
        if flt(row["budgeted_amount"]) else 0
    )


def _budget_lines(filters):
    conditions, params = [], {}
    if filters.get("project"):
        conditions.append("pb.project = %(project)s")
        params["project"] = filters["project"]
    if filters.get("company"):
        conditions.append("pb.company = %(company)s")
        params["company"] = filters["company"]

    where = ("AND " + " AND ".join(conditions)) if conditions else ""
    return frappe.db.sql(
        f"""
        SELECT pb.project, pbi.cost_code, pbi.cost_head, pbi.cost_category,
               pbi.budgeted_amount, pbi.actual_amount, pbi.committed_amount
        FROM `tabProject Budget` pb
        JOIN `tabProject Budget Item` pbi ON pbi.parent = pb.name
        WHERE pb.docstatus = 1 {where}
        """,
        params,
        as_dict=True,
    )


def _tree(filters):
    """Every cost code of the company, as nested dicts, roots first."""
    codes = frappe.get_all(
        "Cost Code",
        filters={"company": filters["company"]} if filters.get("company") else {},
        fields=["name", "cost_code_name", "cost_category", "parent_cost_code", "is_group", "lft"],
        order_by="lft",
    )
    nodes = {
        c.name: {
            "name": c.name, "cost_code_name": c.cost_code_name,
            "cost_category": c.cost_category, "parent": c.parent_cost_code,
            "is_group": c.is_group, "children": [],
        }
        for c in codes
    }
    roots = []
    for c in codes:
        node = nodes[c.name]
        parent = nodes.get(c.parent_cost_code)
        (parent["children"] if parent else roots).append(node)
    return roots


def get_chart(data):
    """The headings, not the leaves — the chart answers the same question the
    tree does."""
    tops = [r for r in data if not flt(r.get("indent"))]
    tops = sorted(tops, key=lambda r: flt(r.get("actual_amount")), reverse=True)[:8]
    if not tops:
        return None
    return {
        "data": {
            "labels": [r["cost_head"] for r in tops],
            "datasets": [
                {"name": _("Budget"), "values": [flt(r["budgeted_amount"]) for r in tops]},
                {"name": _("Actual"), "values": [flt(r["actual_amount"]) for r in tops]},
            ],
        },
        "type": "bar",
    }
