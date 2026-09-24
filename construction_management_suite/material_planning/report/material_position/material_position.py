"""One row per material, from what the bill needs to what the site has used.

The same cement appears in concrete, in blockwork mortar and in plaster, so the
question "how much cement does this job need, and where are we against it" could
not be answered anywhere: the take-off is per BOQ line, the forecast is per
forecast, purchases are per order and consumption is per issue. This adds them
up per item and puts the estimated rate next to what was actually paid.

    required    what the priced lines explode into
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


def _work_label(work, detail):
	"""What the Work column shows, including when nothing named the work.

	A purchase order can legitimately be raised without naming a line of work —
	a general stock top-up. Those quantities are real and must not vanish from
	the report just because they cannot be attributed, so they collect under an
	explicit bucket rather than silently dropping out of every per-work total.
	"""
	if not work:
		return _("(unattributed)")
	labels = [x for x in (detail.get("work_items") or []) if x]
	return labels[0] if labels else work


def build_rows(filters):
	from construction_management_suite.material_planning.doctype.material_consumption_entry.material_consumption_entry import (
		take_off_detail,
		take_off_source,
	)

	project = filters.project
	by_work = bool(filters.get("by_work"))

	if by_work:
		from construction_management_suite.material_planning.doctype.material_consumption_entry.material_consumption_entry import (
			take_off_by_line,
		)
		from construction_management_suite.material_planning.doctype.material_consumption_entry.material_consumption_entry import (
			work_no_map,
		)
		numbers = work_no_map(project)
		take_off = {
			(e["item_code"], e["work_item"]): {
				"item_code": e["item_code"], "uom": e["uom"], "boq_qty": e["qty"],
				"estimated_rate": e["estimated_rate"],
				"work_items": [numbers.get(e["work_item"]) or e["work_item"]],
			}
			for e in take_off_by_line(project, filters.get("boq")).values()
		}
	else:
		take_off = {d["item_code"]: d for d in take_off_detail(project, boq=filters.get("boq"))}
	# What the bill was sold at, beside what the estimate plans to consume. Where
	# the estimate is the source the two can differ — that difference is the
	# point of the column. Where the bill is the source they are the same figure.
	sold = {}
	if take_off_source(project) != "BOQ":
		sold = {
			d["item_code"]: flt(d["boq_qty"])
			for d in take_off_detail(project, boq=filters.get("boq"), source="BOQ")
		}
	forecast = _sum("""
		SELECT i.item_code AS code, i.work_item AS work, SUM(i.net_qty_required) AS qty
		FROM `tabMaterial Forecast Item` i JOIN `tabMaterial Forecast` f ON f.name = i.parent
		WHERE f.project = %(p)s AND f.docstatus = 1
		GROUP BY i.item_code, i.work_item""", project, by_work)
	requested = _sum("""
		SELECT i.item_code AS code, i.cms_work_item AS work, SUM(i.qty) AS qty
		FROM `tabMaterial Request Item` i JOIN `tabMaterial Request` m ON m.name = i.parent
		WHERE i.project = %(p)s AND m.docstatus = 1
		GROUP BY i.item_code, i.cms_work_item""", project, by_work)
	ordered, ordered_value = _sum_value("""
		SELECT i.item_code AS code, i.cms_work_item AS work, SUM(i.qty) AS qty, SUM(i.base_amount) AS value
		FROM `tabPurchase Order Item` i JOIN `tabPurchase Order` o ON o.name = i.parent
		WHERE i.project = %(p)s AND o.docstatus = 1
		GROUP BY i.item_code, i.cms_work_item""", project, by_work)
	# Stock comes in two ways: a Purchase Receipt, or a Purchase Invoice that
	# updates stock — some purchases never get a receipt. ERPNext will not let
	# an invoice update stock when any line came from a receipt, so the two
	# cannot count the same delivery twice.
	received, received_value = _sum_value("""
		SELECT code, work, SUM(qty) AS qty, SUM(value) AS value FROM (
			SELECT i.item_code AS code, i.cms_work_item AS work, i.qty AS qty, i.base_amount AS value
			FROM `tabPurchase Receipt Item` i JOIN `tabPurchase Receipt` r ON r.name = i.parent
			WHERE i.project = %(p)s AND r.docstatus = 1
			UNION ALL
			SELECT i.item_code, i.cms_work_item, i.qty, i.base_amount
			FROM `tabPurchase Invoice Item` i JOIN `tabPurchase Invoice` v ON v.name = i.parent
			WHERE i.project = %(p)s AND v.docstatus = 1 AND v.update_stock = 1
		) x GROUP BY code, work""", project, by_work)

	# What the suppliers have actually billed, receipt or not.
	invoiced, invoiced_value = _sum_value("""
		SELECT i.item_code AS code, i.cms_work_item AS work, SUM(i.qty) AS qty, SUM(i.base_amount) AS value
		FROM `tabPurchase Invoice Item` i JOIN `tabPurchase Invoice` v ON v.name = i.parent
		WHERE i.project = %(p)s AND v.docstatus = 1
		GROUP BY i.item_code, i.cms_work_item""", project, by_work)
	# Material also arrives by transfer, and the Received column read purchase
	# receipts alone — so a store moved onto site showed as never delivered.
	# Older entries carry the project on the header only, newer ones on the row.
	transferred, transferred_value = _sum_value("""
		SELECT i.item_code AS code, i.cms_work_item AS work, SUM(i.qty) AS qty, SUM(i.amount) AS value
		FROM `tabStock Entry Detail` i
		JOIN `tabStock Entry` e ON e.name = i.parent
		LEFT JOIN `tabSite Transfer` t ON t.name = e.cms_site_ref
		WHERE e.docstatus = 1
		  AND e.purpose = 'Material Transfer'
		  AND IFNULL(i.t_warehouse, '') != ''
		  AND (i.project = %(p)s OR (IFNULL(i.project, '') = '' AND e.project = %(p)s))
		  AND IFNULL(t.from_project, '') != %(p)s
		GROUP BY i.item_code, i.cms_work_item""", project, by_work)

	consumed, consumed_value = _sum_value("""
		SELECT i.item_code AS code, i.work_item AS work, SUM(i.qty) AS qty, SUM(i.amount) AS value
		FROM `tabMaterial Consumption Item` i
		JOIN `tabMaterial Consumption Entry` e ON e.name = i.parent
		WHERE e.project = %(p)s AND e.docstatus = 1
		GROUP BY i.item_code, i.work_item""", project, by_work)

	codes = (set(take_off) | set(forecast) | set(requested) | set(ordered)
	         | set(received) | set(transferred) | set(invoiced) | set(consumed))
	group_filter = filters.get("item_group")

	rows = []
	for key in sorted(codes, key=lambda k: (k[0], str(k[1])) if by_work else (k, "")):
		code = key[0] if by_work else key
		work = key[1] if by_work else None
		detail = take_off.get(key) or {}
		item_group = frappe.db.get_value("Item", code, "item_group")
		if group_filter and item_group != group_filter:
			continue

		required = flt(detail.get("boq_qty"))
		est_rate = flt(detail.get("estimated_rate"))
		used = flt(consumed.get(key))
		# Delivered is what was bought in plus what was moved in.
		arrived = flt(received.get(key)) + flt(transferred.get(key))
		# What was actually paid, from receipts where there are any, else orders.
		# A transfer moves stock at valuation and buys nothing, so it is not a rate.
		actual_qty = flt(received.get(key)) or flt(ordered.get(key))
		actual_value = flt(received_value.get(key)) or flt(ordered_value.get(key))
		actual_rate = (actual_value / actual_qty) if actual_qty else 0

		rows.append({
			"item_code": code,
			"work": _work_label(work, detail) if by_work else None,
			"item_group": item_group,
			"uom": detail.get("uom") or frappe.db.get_value("Item", code, "stock_uom"),
			"boq_items": ", ".join(str(x) for x in (detail.get("work_items") or []) if x),
			"required": required,
			"boq_qty": flt(sold.get(key)) if sold else required,
			"forecast": flt(forecast.get(key)),
			"requested": flt(requested.get(key)),
			"ordered": flt(ordered.get(key)),
			"ordered_value": flt(ordered_value.get(key)),
			"received": arrived,
			"transferred_in": flt(transferred.get(key)),
			"received_value": flt(received_value.get(key)) + flt(transferred_value.get(key)),
			"invoiced": flt(invoiced.get(key)),
			"invoiced_value": flt(invoiced_value.get(key)),
			"consumed": used,
			"balance": required - used,
			"est_rate": est_rate,
			"actual_rate": actual_rate,
			"rate_variance_percent": ((actual_rate - est_rate) / est_rate * 100) if est_rate and actual_rate else 0,
			"est_value": required * est_rate,
			"consumed_value": flt(consumed_value.get(key)),
			# Positive means the job is spending more on this material than the
			# bill was priced for, pro-rata to what has been used.
			"value_variance": flt(consumed_value.get(key)) - (used * est_rate),
		})
	return rows


def _key(row, by_work):
	"""Item alone, or item and the line of work it was for."""
	return (row.code, row.get("work") or None) if by_work else row.code


def _sum(sql, project, by_work=False):
	out = {}
	for r in frappe.db.sql(sql, {"p": project}, as_dict=True):
		if not r.code:
			continue
		k = _key(r, by_work)
		out[k] = out.get(k, 0) + flt(r.qty)
	return out


def _sum_value(sql, project, by_work=False):
	qty, value = {}, {}
	for r in frappe.db.sql(sql, {"p": project}, as_dict=True):
		if not r.code:
			continue
		k = _key(r, by_work)
		qty[k] = qty.get(k, 0) + flt(r.qty)
		value[k] = value.get(k, 0) + flt(r.value)
	return qty, value


def get_columns():
	def col(label, fieldname, fieldtype="Float", width=100, **kw):
		return dict(label=_(label), fieldname=fieldname, fieldtype=fieldtype, width=width, **kw)

	return [
		col("Item", "item_code", "Link", 140, options="Item"),
		col("Work", "work", "Data", 80),
		col("Item Group", "item_group", "Link", 120, options="Item Group"),
		col("UOM", "uom", "Link", 90, options="UOM"),
		col("For BOQ Items", "boq_items", "Data", 120),
		col("Required", "required", precision=2, width=110),
		col("BOQ Qty", "boq_qty", precision=2, width=100),
		col("Forecast", "forecast", precision=2),
		col("Requested", "requested", precision=2),
		col("Ordered", "ordered", precision=2),
		col("Ordered Value", "ordered_value", "Currency", 115),
		col("Received", "received", precision=2),
		col("Of Which Moved In", "transferred_in", precision=2, width=130),
		col("Received Value", "received_value", "Currency", 120),
		col("Invoiced", "invoiced", precision=2),
		col("Invoiced Value", "invoiced_value", "Currency", 120),
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
