"""A deliberately small job: estimate, buy, receive, consume. Nothing else.

No BOQ, no Interim Payment Certificate, no Sales Invoice — this company is not
billing a client through the module. No Material Forecast either: the buying is
done straight off the cost estimate, which is the path a forecast is optional
for.

What it does exercise, because these are the cases that behave differently:

  * a **Cost Estimation with no BOQ behind it** — the estimate is the only
    source of the take-off, and `work_no` stays blank because item numbers live
    on a bill that does not exist here
  * material bought **through a Material Request**, and material bought
    **straight off the estimate with no request at all**
  * stock arriving by **Purchase Receipt**, and stock arriving by a
    **Purchase Invoice with Update Stock** — the two are kept on separate
    orders on purpose, because ERPNext refuses an invoice that updates stock
    when any line on it already came through a receipt

    bench --site misk execute \
        construction_management_suite.demo.simple_project.build

Idempotent by name: it refuses to run twice rather than doubling every figure.
"""

import frappe
from frappe.utils import add_days, flt

PROJECT_NAME = "Rusayl Workshop Fit-Out"
COMPANY = "Misk Engineering Services LLC"
CURRENCY = "OMR"
START = "2026-08-03"
TODAY = "2026-09-24"

SUPPLIER = "Rusayl Trading & Supply LLC"
WAREHOUSE = "Stores - ME"

# code, name, uom, buying rate
MATERIALS = [
    ("MES-CEMENT", "Cement OPC 50kg bag", "Nos", 2.30),
    ("MES-AGG", "Aggregate 20mm", "Cubic Meter", 9.00),
    ("MES-REBAR", "Reinforcement bar", "Kg", 0.48),
    ("MES-BEAM", "Structural steel section", "Kg", 0.95),
    ("MES-PIPE-CS", "Carbon steel pipe 100mm", "Meter", 14.50),
    ("MES-CABLE", "XLPE cable 4c x 25mm", "Meter", 6.80),
    ("MES-TRAY", "Cable tray 300mm", "Meter", 11.20),
]

# code, description, uom
WORK_ITEMS = [
    ("MES-FOUND", "Reinforced concrete foundations and plinths", "Cubic Meter"),
    ("MES-STRUCT", "Structural steel fabrication and erection", "Kg"),
    ("MES-PIPING", "Process pipework installation", "Meter"),
    ("MES-ELEC", "Electrical containment and cabling", "Meter"),
]

# The section each line of work sits under — what the breakdown groups by.
SECTIONS = {
    "MES-FOUND": "01 Civil",
    "MES-STRUCT": "02 Structural Steel",
    "MES-PIPING": "03 Mechanical",
    "MES-ELEC": "04 Electrical",
}

# work item, output qty, [(type, material or None, description, qty, rate)]
# Resource quantities are the ABSOLUTE figure for the whole output, and the
# estimate line qty equals output_qty — see the take-off convention.
ANALYSES = [
    ("MES-FOUND", 10, [
        ("Material", "MES-CEMENT", "Cement", 78, 2.30),
        ("Material", "MES-AGG", "Aggregate", 9.2, 9.00),
        ("Material", "MES-REBAR", "Reinforcement", 950, 0.48),
        ("Labour", None, "Concrete gang", 14, 11.00),
        ("Equipment", None, "Mixer and poker", 3, 18.00),
    ]),
    ("MES-STRUCT", 1000, [
        ("Material", "MES-BEAM", "Steel section", 1020, 0.95),
        ("Labour", None, "Fabricator and erector", 22, 13.00),
        ("Equipment", None, "Mobile crane", 4, 45.00),
    ]),
    ("MES-PIPING", 100, [
        ("Material", "MES-PIPE-CS", "CS pipe", 103, 14.50),
        ("Labour", None, "Pipe fitter and welder", 46, 13.00),
    ]),
    ("MES-ELEC", 100, [
        ("Material", "MES-CABLE", "XLPE cable", 104, 6.80),
        ("Material", "MES-TRAY", "Cable tray", 100, 11.20),
        ("Labour", None, "Electrician", 28, 12.00),
    ]),
]

# work item, qty  — equal to output_qty x a whole number of batches
ESTIMATE = [
    ("MES-FOUND", 120),
    ("MES-STRUCT", 18000),
    ("MES-PIPING", 640),
    ("MES-ELEC", 900),
]

# Bought through a Material Request. The rest is bought straight off the
# estimate with no request at all — both paths have to work.
VIA_REQUEST = ("MES-CEMENT", "MES-AGG", "MES-REBAR")


def build():
    if frappe.db.exists("Project", {"project_name": PROJECT_NAME}):
        frappe.throw(f"{PROJECT_NAME} already exists. Delete it first.")
    frappe.flags.ignore_permissions = True
    made = {}

    _supplier()
    _work_items()
    _materials()
    made["project"] = project = _project()
    made["rate_analyses"] = _rate_analyses()
    made["cost_estimation"] = _estimate(project)

    made["material_request"] = request = _material_request(project)
    made["po_from_request"] = po_req = _po_from_request(request)
    made["po_direct"] = po_direct = _po_direct(project)

    # Deliberately one path per order: ERPNext blocks an invoice from updating
    # stock when a line on it already arrived on a receipt.
    made["purchase_receipt"] = _receive_by_receipt(po_req)
    made["purchase_invoice_update_stock"] = _receive_by_invoice(po_direct)

    made["consumption"] = _consumption(project)
    frappe.db.commit()
    return made


# ───────────────────────────── masters ─────────────────────────────


def _item_group(name):
    if not frappe.db.exists("Item Group", name):
        frappe.get_doc({
            "doctype": "Item Group", "item_group_name": name,
            "parent_item_group": "All Item Groups", "is_group": 0,
        }).insert()
    return name


def _supplier():
    if not frappe.db.exists("Supplier", SUPPLIER):
        frappe.get_doc({
            "doctype": "Supplier", "supplier_name": SUPPLIER,
            "supplier_group": frappe.db.get_value("Supplier Group", {"is_group": 0}, "name"),
        }).insert()
    return SUPPLIER


def _work_items():
    """Service items — a line of work is never held in a warehouse."""
    group = _item_group("MES Work")
    for code, desc, uom in WORK_ITEMS:
        if frappe.db.exists("Item", code):
            continue
        frappe.get_doc({
            "doctype": "Item", "item_code": code, "item_name": desc[:140],
            "description": desc, "item_group": group, "stock_uom": uom,
            "is_stock_item": 0, "is_sales_item": 0, "is_purchase_item": 1,
        }).insert()


def _materials():
    group = _item_group("MES Materials")
    for code, name, uom, _rate in MATERIALS:
        if frappe.db.exists("Item", code):
            continue
        frappe.get_doc({
            "doctype": "Item", "item_code": code, "item_name": name,
            "item_group": group, "stock_uom": uom, "is_stock_item": 1,
            "is_purchase_item": 1,
        }).insert()


def _project():
    doc = frappe.get_doc({
        "doctype": "Project", "project_name": PROJECT_NAME, "company": COMPANY,
        "currency": CURRENCY, "status": "Open", "expected_start_date": START,
        "cms_project_type": "Infrastructure",
        "cost_center": frappe.db.get_value("Company", COMPANY, "cost_center"),
        # One site store, so every material document fills it in on its own.
        "cms_default_warehouse": WAREHOUSE,
    })
    doc.insert()
    return doc.name


def _rate_analyses():
    made = {}
    for item, output, resources in ANALYSES:
        uom = dict((c, u) for c, _d, u in WORK_ITEMS)[item]
        doc = frappe.get_doc({
            "doctype": "Rate Analysis", "analysis_name": f"{item} — per {output} {uom.lower()}",
            "item_code": item, "uom": uom, "company": COMPANY, "currency": CURRENCY,
            "date": START, "output_qty": output, "status": "Draft", "rate_basis": "Manual",
        })
        for rtype, ritem, desc, qty, rate in resources:
            doc.append("resources", {
                "resource_type": rtype, "resource_item": ritem,
                "description": desc, "qty": qty, "rate": rate,
            })
        doc.insert()
        doc.status = "Approved"
        doc.is_default = 1
        doc.save()
        made[item] = doc.name
    return made


# ───────────────────────── the only priced document ─────────────────────────


def _estimate(project):
    """The cost plan. With no BOQ on this job it is the only source there is."""
    from construction_management_suite.api.boq import get_rate_analysis_for_item

    doc = frappe.get_doc({
        "doctype": "Cost Estimation", "project": project, "company": COMPANY,
        "currency": CURRENCY, "estimation_date": add_days(START, 3),
        "estimation_title": f"Cost estimate — {PROJECT_NAME}",
        "contingency_percent": 4,
    })
    for code, qty in ESTIMATE:
        uom = dict((c, u) for c, _d, u in WORK_ITEMS)[code]
        doc.append("items", {
            "item_code": code, "qty": qty, "uom": uom,
            "boq_section": SECTIONS.get(code),
            "description": dict((c, d) for c, d, _u in WORK_ITEMS)[code],
            "rate_analysis_ref": get_rate_analysis_for_item(code, COMPANY),
        })
    doc.insert()
    doc.submit()
    return doc.name


# ───────────────────────────── buying ─────────────────────────────


def _outstanding(project):
    from construction_management_suite.api.boq import take_off_outstanding

    return {r["item_code"]: r for r in take_off_outstanding(project)}


def _material_request(project):
    """The formal route: the site asks, then someone orders."""
    lines = _outstanding(project)
    mr = frappe.get_doc({
        "doctype": "Material Request", "material_request_type": "Purchase",
        "company": COMPANY, "transaction_date": add_days(START, 10),
        "schedule_date": add_days(START, 30),
    })
    for code in VIA_REQUEST:
        line = lines.get(code)
        if not line:
            continue
        mr.append("items", {
            "item_code": code, "qty": line["qty"], "uom": line["uom"],
            "warehouse": WAREHOUSE, "project": project,
            "schedule_date": add_days(START, 30),
            "cms_work_item": line["cms_work_item"],
        })
    mr.insert()
    mr.submit()
    return mr.name


def _po_from_request(request):
    from erpnext.stock.doctype.material_request.material_request import make_purchase_order

    po = make_purchase_order(request)
    po.supplier = SUPPLIER
    po.transaction_date = add_days(START, 14)
    po.schedule_date = add_days(START, 34)
    for row in po.items:
        row.rate = dict((m[0], m[3]) for m in MATERIALS).get(row.item_code, row.rate)
        row.warehouse = WAREHOUSE
    po.insert()
    po.submit()
    return po.name


def _po_direct(project):
    """Bought straight off the estimate — no request in between.

    This is the path that had no work reference on it at all before: nothing
    filled `cms_work_item`, so what was needed and what was bought never met in
    a report.
    """
    lines = _outstanding(project)
    po = frappe.get_doc({
        "doctype": "Purchase Order", "supplier": SUPPLIER, "company": COMPANY,
        "currency": CURRENCY, "transaction_date": add_days(START, 16),
        "schedule_date": add_days(START, 40),
    })
    for code, line in sorted(lines.items()):
        if code in VIA_REQUEST:
            continue
        po.append("items", {
            "item_code": code, "qty": line["qty"], "uom": line["uom"],
            "rate": dict((m[0], m[3]) for m in MATERIALS).get(code, 0),
            "warehouse": WAREHOUSE, "project": project,
            "schedule_date": add_days(START, 40),
            "cms_work_item": line["cms_work_item"],
        })
    po.insert()
    po.submit()
    return po.name


# ───────────────────────────── receiving ─────────────────────────────


def _receive_by_receipt(purchase_order):
    """Stock in on a Purchase Receipt — the ordinary route."""
    from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt

    from construction_management_suite.utils.billing import orderable_qty

    pr = make_purchase_receipt(purchase_order)
    pr.posting_date = add_days(START, 36)
    pr.set_posting_time = 1
    for row in pr.items:
        row.warehouse = WAREHOUSE
        # Part delivery, so the report has an outstanding balance to show.
        # Rounded, because a whole-number UOM will not take 561.6 bags.
        row.qty = orderable_qty(flt(row.qty) * 0.6, row.uom)
        row.received_qty = row.qty
    pr.insert()
    pr.submit()
    return pr.name


def _receive_by_invoice(purchase_order):
    """Stock in on the invoice itself — some purchases never get a receipt.

    Kept on its own order deliberately: ERPNext refuses `update_stock` on an
    invoice when any line on it already arrived through a receipt.
    """
    from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_invoice

    from construction_management_suite.utils.billing import orderable_qty

    pi = make_purchase_invoice(purchase_order)
    pi.posting_date = add_days(START, 42)
    pi.set_posting_time = 1
    pi.bill_no = "RTS/2026/8841"
    pi.bill_date = add_days(START, 42)
    pi.update_stock = 1
    for row in pi.items:
        row.warehouse = WAREHOUSE
        row.qty = orderable_qty(flt(row.qty) * 0.5, row.uom)
        row.received_qty = row.qty
    pi.insert()
    pi.submit()
    return pi.name


# ───────────────────────────── using it ─────────────────────────────


def _consumption(project):
    """Issued to site, each row naming the line of work it went into."""
    from construction_management_suite.material_planning.doctype.material_consumption_entry.material_consumption_entry import (
        take_off_by_line,
    )

    work_of = {}
    for (code, work_item), _entry in take_off_by_line(project).items():
        work_of.setdefault(code, work_item)

    rates = dict((m[0], m[3]) for m in MATERIALS)
    made = []
    for date, rows in (
        (add_days(START, 44), [("MES-CEMENT", 240), ("MES-AGG", 28), ("MES-REBAR", 2900)]),
        (add_days(START, 51), [("MES-BEAM", 4200), ("MES-PIPE-CS", 120)]),
    ):
        doc = frappe.get_doc({
            "doctype": "Material Consumption Entry", "project": project,
            "company": COMPANY, "warehouse": WAREHOUSE, "posting_date": date,
        })
        for code, qty in rows:
            if not work_of.get(code):
                continue
            doc.append("items", {
                "item_code": code, "qty": qty,
                "uom": frappe.db.get_value("Item", code, "stock_uom"),
                "valuation_rate": rates.get(code, 0),
                "work_item": work_of[code],
            })
        if not doc.items:
            continue
        doc.insert()
        doc.submit()
        made.append(doc.name)
    return made
