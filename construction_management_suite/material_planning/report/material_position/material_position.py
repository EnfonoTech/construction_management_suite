"""One row per material, from what the bill needs to what the site has used.

The same cement appears in concrete, in blockwork mortar and in plaster, so the
question "how much cement does this job need, and where are we against it" could
not be answered anywhere: the take-off is per BOQ line, the forecast is per
forecast, purchases are per order and consumption is per issue. This adds them
up per item and puts the estimated rate next to what was actually paid.

    required    what the priced bills explode into, waste included
    forecast    what has been planned for ordering
    requested   on submitted material requests
    ordered     on submitted purchase orders
    received    actually delivered
    consumed    issued to the site
    balance     required less consumed — what the job still has to use
"""

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("project"):
		frappe.throw(_("Choose a project"))

	rows = build_rows(filters)
	if filters.get("only_over"):
		rows = [r for r in rows if flt(r["consumed"]) > flt(r["required"]) + 0.0001]
	return get_columns(), rows, None, get_chart(rows), get_summary(rows)


def build_rows(filters):
	from construction_management_suite.material_planning.doctype.material_consumption_entry.material_consumption_entry import (
		take_off_detail,
	)

	project = filters.project
	take_off = {d["item_code"]: d for d in take_off_detail(project, boq=filters.get("boq"))}
	forecast = _sum("""
		SELECT i.item_code AS code, SUM(i.net_qty_required) AS qty
		FROM `tabMaterial Forecast Item` i JOIN `tabMaterial Forecast` f ON f.name = i.parent
		WHERE f.project = %(p)s AND f.docstatus = 1 GROUP BY i.item_code""", project)
	requested = _sum("""
		SELECT i.item_code AS code, SUM(i.qty) AS qty
		FROM `tabMaterial Request Item` i JOIN `tabMaterial Request` m ON m.name = i.parent
		WHERE i.project = %(p)s AND m.docstatus = 1 GROUP BY i.item_code""", project)
	ordered, ordered_value = _sum_value("""
		SELECT i.item_code AS code, SUM(i.qty) AS qty, SUM(i.base_amount) AS value
		FROM `tabPurchase Order Item` i JOIN `tabPurchase Order` o ON o.name = i.parent
		WHERE i.project = %(p)s AND o.docstatus = 1 GROUP BY i.item_code""", project)
	received, received_value = _sum_value("""
		SELECT i.item_code AS code, SUM(i.qty) AS qty, SUM(i.base_amount) AS value
		FROM `tabPurchase Receipt Item` i JOIN `tabPurchase Receipt` r ON r.name = i.parent
		WHERE i.project = %(p)s AND r.docstatus = 1 GROUP BY i.item_code""", project)
	consumed, consumed_value = _sum_value("""
		SELECT i.item_code AS code, SUM(i.qty) AS qty, SUM(i.amount) AS value
		FROM `tabMaterial Consumption Item` i
		JOIN `tabMaterial Consumption Entry` e ON e.name = i.parent
		WHERE e.project = %(p)s AND e.docstatus = 1 GROUP BY i.item_code""", project)

	codes = set(take_off) | set(forecast) | set(requested) | set(ordered) | set(received) | set(consumed)
	group_filter = filters.get("item_group")

	rows = []
	for code in sorted(codes):
		detail = take_off.get(code) or {}
		item_group = frappe.db.get_value("Item", code, "item_group")
		if group_filter and item_group != group_filter:
			continue

		required = flt(detail.get("boq_qty")) * (1 + flt(detail.get("waste_factor")) / 100)
		est_rate = flt(detail.get("estimated_rate"))
		used = flt(consumed.get(code))
		# What was actually paid, from receipts where there are any, else orders.
		actual_qty = flt(received.get(code)) or flt(ordered.get(code))
		actual_value = flt(received_value.get(code)) or flt(ordered_value.get(code))
		actual_rate = (actual_value / actual_qty) if actual_qty else 0

		rows.append({
			"item_code": code,
			"item_group": item_group,
			"uom": detail.get("uom") or frappe.db.get_value("Item", code, "stock_uom"),
			"boq_items": ", ".join(detail.get("boq_items") or []),
			"required": required,
			"forecast": flt(forecast.get(code)),
			"requested": flt(requested.get(code)),
			"ordered": flt(ordered.get(code)),
			"received": flt(received.get(code)),
			"consumed": used,
			"balance": required - used,
			"est_rate": est_rate,
			"actual_rate": actual_rate,
			"rate_variance_percent": ((actual_rate - est_rate) / est_rate * 100) if est_rate and actual_rate else 0,
			"est_value": required * est_rate,
			"consumed_value": flt(consumed_value.get(code)),
			# Positive means the job is spending more on this material than the
			# bill was priced for, pro-rata to what has been used.
			"value_variance": flt(consumed_value.get(code)) - (used * est_rate),
		})
	return rows


def _sum(sql, project):
	return {r.code: flt(r.qty) for r in frappe.db.sql(sql, {"p": project}, as_dict=True) if r.code}


def _sum_value(sql, project):
	qty, value = {}, {}
	for r in frappe.db.sql(sql, {"p": project}, as_dict=True):
		if not r.code:
			continue
		qty[r.code] = flt(r.qty)
		value[r.code] = flt(r.value)
	return qty, value


def get_columns():
	def col(label, fieldname, fieldtype="Float", width=100, **kw):
		return dict(label=_(label), fieldname=fieldname, fieldtype=fieldtype, width=width, **kw)

	return [
		col("Item", "item_code", "Link", 140, options="Item"),
		col("Item Group", "item_group", "Link", 120, options="Item Group"),
		col("UOM", "uom", "Link", 90, options="UOM"),
		col("For BOQ Items", "boq_items", "Data", 120),
		col("Required", "required", precision=2, width=110),
		col("Forecast", "forecast", precision=2),
		col("Requested", "requested", precision=2),
		col("Ordered", "ordered", precision=2),
		col("Received", "received", precision=2),
		col("Consumed", "consumed", precision=2, width=110),
		col("Balance", "balance", precision=2, width=110),
		col("Est. Rate", "est_rate", "Currency"),
		col("Actual Rate", "actual_rate", "Currency"),
		col("Rate Var %", "rate_variance_percent", "Percent", 95),
		col("Est. Value", "est_value", "Currency", 120),
		col("Consumed Value", "consumed_value", "Currency", 130),
		col("Value Variance", "value_variance", "Currency", 130),
	]


def get_summary(rows):
	est = sum(flt(r["est_value"]) for r in rows)
	used = sum(flt(r["consumed_value"]) for r in rows)
	over = [r for r in rows if flt(r["consumed"]) > flt(r["required"]) + 0.0001]
	return [
		{"label": _("Take-off Value"), "value": est, "datatype": "Currency"},
		{"label": _("Consumed Value"), "value": used, "datatype": "Currency"},
		{
			"label": _("Variance"), "value": used - est, "datatype": "Currency",
			"indicator": "Red" if used > est else "Green",
		},
		{
			"label": _("Over-consumed Items"), "value": len(over), "datatype": "Int",
			"indicator": "Red" if over else "Green",
		},
	]


def get_chart(rows):
	top = sorted(rows, key=lambda r: flt(r["est_value"]), reverse=True)[:8]
	if not top:
		return None
	return {
		"data": {
			"labels": [r["item_code"] for r in top],
			"datasets": [
				{"name": _("Required"), "values": [flt(r["required"]) for r in top]},
				{"name": _("Consumed"), "values": [flt(r["consumed"]) for r in top]},
			],
		},
		"type": "bar",
		"colors": ["#7cd6fd", "#ff5858"],
	}
