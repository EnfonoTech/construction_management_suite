"""Explode a Bill of Quantities into the resources underneath it.

A BOQ says "180 m³ of concrete at 62.500". This report says what that is made
of and, more usefully, how much of each thing the whole bill needs — the
material take-off. The column that does that work is `total_qty`:

    total_qty = boq_qty x qty_per_unit x (1 + waste_factor/100)

Rates are read from the frozen `rate_build_up` on the BOQ line wherever one
exists, so the report shows what the line was actually priced at rather than
what the rate library says today. Where a line has no snapshot the live
analysis is used and the row is labelled as such; where it has no analysis at
all the line still appears, because a take-off that silently drops two thirds
of a bill is worse than no take-off.
"""

import json

import frappe
from frappe import _
from frappe.utils import flt

AS_PRICED = "As priced"
LIVE = "Live analysis"
NO_ANALYSIS = "No analysis"

UNLINKED_SUFFIX = " (not linked to an Item)"

GROUP_BY_FIELD = {
	"BOQ Item": "boq_item",
	"Resource Item": "resource_key",
	"Item Group": "item_group",
	"Resource Type": "resource_type",
	"Work Category": "work_category",
	"BOQ Section": "boq_section",
}


def execute(filters=None):
	filters = frappe._dict(filters or {})
	# The checkbox defaults to on in the .js, but a server-side run — a script,
	# an export, a scheduled job — passes no filters at all and was silently
	# getting the unexploded bill.
	if "show_resources" not in filters:
		filters.show_resources = 1
	columns = get_columns(filters)
	rows = get_rows(filters)

	group_by = GROUP_BY_FIELD.get(filters.get("group_by"))
	if not group_by:
		return columns, rows, None, get_chart(rows), None, 0

	rows = group_rows(rows, group_by)
	move_column_first(columns, group_by)
	# Subtotals are emitted per group, so the framework's own total row would
	# double-count everything.
	return columns, rows, None, get_chart(rows), None, 1


# ───────────────────────────── columns ─────────────────────────────


def get_columns(filters):
	columns = [
		{"label": _("BOQ"), "fieldname": "boq", "fieldtype": "Link", "options": "BOQ", "width": 130},
		{"label": _("Section"), "fieldname": "boq_section", "fieldtype": "Data", "width": 120},
		{"label": _("Work Category"), "fieldname": "work_category", "fieldtype": "Data", "width": 110},
		{"label": _("BOQ Item"), "fieldname": "boq_item", "fieldtype": "Link", "options": "Item", "width": 130},
		{"label": _("Description"), "fieldname": "description", "fieldtype": "Data", "width": 220},
		{"label": _("BOQ Qty"), "fieldname": "boq_qty", "fieldtype": "Float", "width": 90},
		{"label": _("UOM"), "fieldname": "boq_uom", "fieldtype": "Link", "options": "UOM", "width": 70},
		{"label": _("BOQ Rate"), "fieldname": "boq_rate", "fieldtype": "Currency", "options": "currency", "width": 100},
		{"label": _("BOQ Amount"), "fieldname": "boq_amount", "fieldtype": "Currency", "options": "currency", "width": 120},
	]
	if not filters.get("show_resources"):
		return columns

	return columns + [
		{"label": _("Type"), "fieldname": "resource_type", "fieldtype": "Data", "width": 90},
		{"label": _("Resource Item"), "fieldname": "resource_item", "fieldtype": "Link", "options": "Item", "width": 130},
		{"label": _("Item Group"), "fieldname": "item_group", "fieldtype": "Link", "options": "Item Group", "width": 120},
		{"label": _("Resource"), "fieldname": "resource_description", "fieldtype": "Data", "width": 200},
		{"label": _("Res. UOM"), "fieldname": "resource_uom", "fieldtype": "Link", "options": "UOM", "width": 80},
		{"label": _("Qty / Unit"), "fieldname": "qty_per_unit", "fieldtype": "Float", "precision": 4, "width": 90},
		{"label": _("Waste %"), "fieldname": "waste_factor", "fieldtype": "Percent", "width": 80},
		{"label": _("Total Qty"), "fieldname": "total_qty", "fieldtype": "Float", "precision": 3, "width": 110},
		{"label": _("Rate"), "fieldname": "resource_rate", "fieldtype": "Currency", "options": "currency", "width": 100},
		{"label": _("Amount"), "fieldname": "resource_amount", "fieldtype": "Currency", "options": "currency", "width": 120},
		{"label": _("Source"), "fieldname": "source", "fieldtype": "Data", "width": 100},
		{"label": _("Currency"), "fieldname": "currency", "fieldtype": "Link", "options": "Currency", "hidden": 1, "width": 80},
		# The grouping key for "Resource Item". Synthetic, because a row with no
		# Item still has to group under something a human can read. Hidden until
		# it is the dimension being grouped by.
		{"label": _("Resource"), "fieldname": "resource_key", "fieldtype": "Data", "hidden": 1, "width": 240},
	]


# ───────────────────────────── data ─────────────────────────────


def get_rows(filters):
	lines = get_boq_lines(filters)
	if not filters.get("show_resources"):
		return lines

	rows = []
	for line in lines:
		resources = get_resources_for_line(line)
		if not resources:
			line["resource_type"] = ""
			line["resource_description"] = _("No rate analysis on this line")
			line["source"] = NO_ANALYSIS
			line["resource_key"] = _("(no analysis)")
			rows.append(line)
			continue

		for res in resources:
			if not passes_resource_filters(res, filters):
				continue
			rows.append(build_row(line, res))

	return rows


def get_boq_lines(filters):
	conditions = ["b.docstatus < 2"]
	params = {}
	for field, column in (
		("company", "b.company"),
		("project", "b.project"),
		("boq", "b.name"),
	):
		if filters.get(field):
			conditions.append(f"{column} = %({field})s")
			params[field] = filters[field]

	return frappe.db.sql(
		"""
		SELECT
			b.name          AS boq,
			b.currency      AS currency,
			i.name          AS boq_item_row,
			i.idx           AS boq_idx,
			i.item_code     AS boq_item,
			i.description   AS description,
			i.uom           AS boq_uom,
			i.qty           AS boq_qty,
			i.rate          AS boq_rate,
			i.amount        AS boq_amount,
			i.boq_section   AS boq_section,
			i.work_category AS work_category,
			i.rate_analysis_ref AS rate_analysis_ref,
			i.rate_build_up AS rate_build_up
		FROM `tabBOQ Item` i
		JOIN `tabBOQ` b ON b.name = i.parent
		WHERE {conditions}
		ORDER BY b.name, i.idx
		""".format(conditions=" AND ".join(conditions)),
		params,
		as_dict=True,
	)


def get_resources_for_line(line):
	"""Prefer what the line was priced at; fall back to the library as it is now."""
	if line.get("rate_build_up"):
		try:
			frozen = json.loads(line["rate_build_up"])
		except (ValueError, TypeError):
			frozen = None
		if frozen and frozen.get("resources"):
			for res in frozen["resources"]:
				res["_source"] = AS_PRICED
				res["_output_qty"] = flt(frozen.get("output_qty")) or 1
			return frozen["resources"]

	if not line.get("rate_analysis_ref"):
		return []
	if not frappe.db.exists("Rate Analysis", line["rate_analysis_ref"]):
		return []

	ra = frappe.get_cached_doc("Rate Analysis", line["rate_analysis_ref"])
	output_qty = flt(ra.output_qty) or 1
	return [
		{
			"type": r.resource_type,
			"resource_item": r.resource_item or None,
			"item_group": (
				frappe.db.get_value("Item", r.resource_item, "item_group") if r.resource_item else None
			),
			"description": r.description or r.resource_item,
			"uom": r.uom,
			"qty": flt(r.qty),
			"rate": flt(r.rate),
			"waste_factor": flt(r.waste_factor),
			"_source": LIVE,
			"_output_qty": output_qty,
		}
		for r in ra.resources
	]


def passes_resource_filters(res, filters):
	if filters.get("resource_type") and res.get("type") != filters["resource_type"]:
		return False
	if filters.get("item_group") and res.get("item_group") != filters["item_group"]:
		return False
	return True


def build_row(line, res):
	row = dict(line)
	# An analysis priced for a batch states its resources for the whole batch,
	# so bring them back to one unit before scaling by the BOQ quantity.
	output_qty = flt(res.get("_output_qty")) or 1
	qty_per_unit = flt(res.get("qty")) / output_qty
	waste = flt(res.get("waste_factor"))
	total_qty = flt(line.get("boq_qty")) * qty_per_unit * (1 + waste / 100)

	row.update(
		{
			"resource_type": res.get("type"),
			"resource_item": res.get("resource_item"),
			"item_group": res.get("item_group"),
			"resource_description": res.get("description"),
			"resource_uom": res.get("uom"),
			"qty_per_unit": qty_per_unit,
			"waste_factor": waste,
			"total_qty": total_qty,
			"resource_rate": flt(res.get("rate")),
			"resource_amount": total_qty * flt(res.get("rate")),
			"source": res.get("_source"),
			# Grouping by "Resource Item" has to cope with rows that name no
			# Item — most of them, today — so fall back to the description and
			# say why rather than bucketing everything under a blank.
			"resource_key": res.get("resource_item")
			or ((res.get("description") or _("Unnamed resource")) + UNLINKED_SUFFIX),
		}
	)
	return row


# ───────────────────────────── grouping ─────────────────────────────


def group_rows(rows, group_by):
	"""Bucket by one dimension and emit a bold subtotal after each group."""
	buckets = {}
	for row in rows:
		buckets.setdefault(row.get(group_by) or _("(none)"), []).append(row)

	grouped = []
	for key in sorted(buckets, key=lambda k: str(k)):
		members = buckets[key]
		grouped += members
		grouped.append(subtotal_row(members, group_by, key))
		grouped.append({})
	return grouped


def subtotal_row(members, group_by, key):
	row = {
		group_by: key,
		"boq_amount": sum(flt(r.get("boq_amount")) for r in members),
		"resource_amount": sum(flt(r.get("resource_amount")) for r in members),
		"currency": members[0].get("currency") if members else None,
		"bold": 1,
	}
	# Summing a quantity only means something when the unit is the same all the
	# way down the group; otherwise it would add kilograms to cubic metres.
	uoms = {r.get("resource_uom") for r in members if r.get("resource_uom")}
	if len(uoms) == 1:
		row["total_qty"] = sum(flt(r.get("total_qty")) for r in members)
		row["resource_uom"] = uoms.pop()
	return row


def move_column_first(columns, fieldname):
	index = next((i for i, c in enumerate(columns) if c["fieldname"] == fieldname), None)
	if index is None:
		return
	column = columns.pop(index)
	column["hidden"] = 0
	columns.insert(0, column)


# ───────────────────────────── chart ─────────────────────────────


def get_chart(rows):
	totals = {}
	for row in rows:
		if row.get("bold") or not row.get("resource_type"):
			continue
		totals[row["resource_type"]] = totals.get(row["resource_type"], 0) + flt(row.get("resource_amount"))
	totals = {k: v for k, v in totals.items() if v}
	if not totals:
		return None
	return {
		"data": {
			"labels": list(totals.keys()),
			"datasets": [{"name": _("Resource Cost"), "values": [totals[k] for k in totals]}],
		},
		"type": "donut",
	}
