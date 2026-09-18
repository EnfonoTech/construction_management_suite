"""Build one construction project, mid-flight, with every document type on it.

Run it to get a site you can actually look at: a bill priced from the rate
library, a budget, a subcontractor part-paid, two certificates issued and a
third being prepared, an approved variation, materials bought and consumed, and
a fortnight of site reports.

    bench --site misk execute \
        construction_management_suite.demo.sample_project.build

Idempotent by name: it refuses to run twice for the same project rather than
quietly doubling every figure.
"""

import frappe
from frappe.utils import add_days, flt, getdate

PROJECT_NAME = "Al Khuwair Office Block"
COMPANY = "misk"
CURRENCY = "OMR"
START = "2026-05-04"
TODAY = "2026-09-18"

# What the contractor actually buys, and what it costs.
MATERIALS = [
    ("CMS-CEMENT-OPC", "Cement OPC 50kg bag", "Nos", 2.10),
    ("CMS-AGG-20", "Aggregate 20mm", "Cubic Meter", 8.50),
    ("CMS-SAND", "Washed sand", "Cubic Meter", 6.00),
    ("CMS-STEEL", "Reinforcement steel", "Kg", 0.45),
    ("CMS-BLOCK-200", "200mm hollow block", "Nos", 0.42),
]

# item, uom, output qty, [(type, item_or_none, description, qty, rate, waste)]
ANALYSES = [
    ("CMS-RCC-M30", "Cubic Meter", 10, [
        ("Material", "CMS-CEMENT-OPC", "Cement", 70, 2.10, 5),
        ("Material", "CMS-AGG-20", "Aggregate", 8.5, 8.50, 5),
        ("Material", "CMS-SAND", "Sand", 4.5, 6.00, 5),
        ("Labour", None, "Concrete gang", 6, 12.00, 0),
        ("Equipment", None, "Mixer and vibrator", 2.5, 25.00, 0),
        ("Overhead", None, "Site overhead", 1, 35.00, 0),
    ]),
    ("CMS-BLK-200", "Square Meter", 100, [
        ("Material", "CMS-BLOCK-200", "200mm blocks", 1250, 0.42, 3),
        ("Material", "CMS-CEMENT-OPC", "Mortar cement", 42, 2.10, 5),
        ("Material", "CMS-SAND", "Mortar sand", 3.2, 6.00, 5),
        ("Labour", None, "Mason and helper", 34, 12.00, 0),
        ("Overhead", None, "Site overhead", 1, 40.00, 0),
    ]),
    ("CMS-PLASTER", "Square Meter", 100, [
        ("Material", "CMS-CEMENT-OPC", "Plaster cement", 26, 2.10, 5),
        ("Material", "CMS-SAND", "Plaster sand", 2.6, 6.00, 5),
        ("Labour", None, "Plasterer", 22, 12.00, 0),
    ]),
]

# item, description, section, category, uom, qty, rate
BOQ_LINES = [
    ("CMS-EXCAV", "Bulk excavation in ordinary soil", "01 Substructure", "Civil", "Cubic Meter", 1250, 3.80),
    ("CMS-PCC", "Plain cement concrete blinding 100mm", "01 Substructure", "Civil", "Cubic Meter", 95, 44.00),
    ("CMS-RCC-M30", "Reinforced concrete C30/20 to foundations and frame", "01 Substructure", "Civil", "Cubic Meter", 640, 82.00),
    ("CMS-REBAR", "High tensile reinforcement steel", "01 Substructure", "Civil", "Kg", 78000, 0.58),
    ("CMS-BLK-200", "200mm hollow block walling", "02 Superstructure", "Civil", "Square Meter", 3400, 11.50),
    ("CMS-PLASTER", "Internal cement plaster 15mm", "03 Finishes", "Finishes", "Square Meter", 6800, 4.20),
    ("CMS-TILE", "Porcelain floor tiling 600x600", "03 Finishes", "Finishes", "Square Meter", 1850, 18.50),
    ("CMS-PAINT", "Emulsion paint, three coats", "03 Finishes", "Finishes", "Square Meter", 8200, 3.10),
    ("CMS-ALUM", "Aluminium glazed windows and doors", "03 Finishes", "Finishes", "Square Meter", 520, 76.00),
    ("CMS-ELEC", "Electrical installation, complete", "04 MEP", "Electrical", "Nos", 1, 78000.00),
    ("CMS-PLUMB", "Plumbing and drainage, complete", "04 MEP", "Plumbing", "Nos", 1, 62000.00),
    ("CMS-EXT", "External works, parking and landscaping", "05 External", "External Works", "Nos", 1, 41000.00),
]


def build():
    """Create everything. Refuses to run twice."""
    if frappe.db.exists("Project", {"project_name": PROJECT_NAME}):
        frappe.throw(
            f"{PROJECT_NAME} already exists. Delete it first, or change PROJECT_NAME."
        )
    frappe.flags.ignore_permissions = True
    made = {}

    made["client"] = client = _customer()
    made["subcontractor"] = sub = _supplier()
    made["warehouse"] = warehouse = _warehouse()
    _materials()

    made["project"] = project = _project(client)
    made["rate_analyses"] = analyses = _rate_analyses()
    made["boq"] = boq = _boq(project, client, analyses)
    made["cost_estimation"] = _cost_estimation(project, boq)
    made["subcontract"] = agreement = _subcontract(project, sub)
    made["work_order"] = _work_order(project, sub, agreement)
    made["sub_certificate"] = _sub_certificate(project, sub, agreement)
    made["ipcs"] = _certificates(project, client, boq)
    made["variation"] = _variation(project, client, boq)
    made["forecast"] = _forecast(project, boq)
    made["stock"] = _seed_stock(warehouse)
    made["consumption"] = _consumption(project, warehouse)
    made["site_reports"] = _site_reports(project)

    frappe.db.commit()
    return made


# ───────────────────────────── masters ─────────────────────────────


def _customer():
    name = "Al Khuwair Properties LLC"
    if not frappe.db.exists("Customer", name):
        frappe.get_doc({
            "doctype": "Customer", "customer_name": name,
            "customer_group": frappe.db.get_value("Customer Group", {"is_group": 0}, "name"),
            "territory": frappe.db.get_value("Territory", {"is_group": 0}, "name"),
        }).insert()
    return name


def _supplier():
    name = "Gulf Blockwork Contracting"
    if not frappe.db.exists("Supplier", name):
        frappe.get_doc({
            "doctype": "Supplier", "supplier_name": name,
            "supplier_group": frappe.db.get_value("Supplier Group", {"is_group": 0}, "name"),
        }).insert()
    return name


def _warehouse():
    name = f"Al Khuwair Site Store - {frappe.get_cached_value('Company', COMPANY, 'abbr')}"
    if not frappe.db.exists("Warehouse", name):
        frappe.get_doc({
            "doctype": "Warehouse", "warehouse_name": "Al Khuwair Site Store",
            "company": COMPANY,
            "parent_warehouse": frappe.db.get_value(
                "Warehouse", {"company": COMPANY, "is_group": 1}, "name"),
        }).insert()
    return name


def _materials():
    """Make sure the purchasable materials exist and are stock items."""
    group = "Services" if frappe.db.exists("Item Group", "Services") else "All Item Groups"
    for code, name, uom, _rate in MATERIALS:
        if frappe.db.exists("Item", code):
            continue
        frappe.get_doc({
            "doctype": "Item", "item_code": code, "item_name": name,
            "item_group": group, "stock_uom": uom, "is_stock_item": 1,
        }).insert()


def _project(client):
    doc = frappe.get_doc({
        "doctype": "Project", "project_name": PROJECT_NAME, "company": COMPANY,
        "customer": client, "currency": CURRENCY, "status": "Open",
        "expected_start_date": START, "expected_end_date": add_days(START, 540),
        "cms_project_type": "Building Construction",
        "cms_client_po": "AKP/CONT/2026/07",
        "cms_retention_percent": 5,
    })
    doc.insert()
    return doc.name


def _rate_analyses():
    made = {}
    for item, uom, output, resources in ANALYSES:
        doc = frappe.get_doc({
            "doctype": "Rate Analysis",
            "analysis_name": f"{item} — per {uom.lower()}",
            "item_code": item, "uom": uom, "company": COMPANY, "currency": CURRENCY,
            "date": START, "output_qty": output, "status": "Draft", "rate_basis": "Manual",
        })
        for rtype, ritem, desc, qty, rate, waste in resources:
            doc.append("resources", {
                "resource_type": rtype, "resource_item": ritem, "description": desc,
                "qty": qty, "rate": rate, "waste_factor": waste,
            })
        doc.insert()
        doc.status = "Approved"
        doc.is_default = 1
        doc.save()
        made[item] = doc.name
    return made


# ───────────────────────────── the contract ─────────────────────────────


def _boq(project, client, analyses):
    from construction_management_suite.api.boq import get_rate_analysis_rates

    doc = frappe.get_doc({
        "doctype": "BOQ", "project": project, "company": COMPANY, "currency": CURRENCY,
        "client": client, "client_po": "AKP/CONT/2026/07", "boq_date": START,
        "contract_date": START, "rate_source": "Rate Analysis",
    })
    for code, desc, section, category, uom, qty, rate in BOQ_LINES:
        row = {"item_code": code, "description": desc, "boq_section": section,
               "work_category": category, "uom": uom, "qty": qty, "rate": rate}
        if code in analyses:
            row["rate_analysis_ref"] = analyses[code]
        doc.append("items", row)
    doc.insert()
    doc.submit()
    frappe.db.set_value("Project", project, "cms_contract_value", flt(doc.grand_total))
    return doc.name


def _cost_estimation(project, boq):
    from construction_management_suite.api.boq import make_cost_estimation

    doc = make_cost_estimation(boq)
    doc.estimation_date = add_days(START, 2)
    doc.selling_price = flt(frappe.db.get_value("BOQ", boq, "grand_total"))
    doc.insert()
    doc.submit()
    return doc.name


def _certificates(project, client, boq):
    """Two issued, one being prepared — a project part way through."""
    from construction_management_suite.api.boq import get_boq_lines_for_ipc

    periods = [("2026-06-01", "2026-06-30", 0.18), ("2026-07-01", "2026-07-31", 0.22),
               ("2026-08-01", "2026-08-31", 0.16)]
    made = []
    for n, (start, end, share) in enumerate(periods, start=1):
        doc = frappe.get_doc({
            "doctype": "Interim Payment Certificate", "project": project, "company": COMPANY,
            "currency": CURRENCY, "client": client, "boq_ref": boq, "ipc_number": n,
            "billing_period_from": start, "billing_period_to": end,
        })
        for line in get_boq_lines_for_ipc(boq):
            line["qty_this_period"] = round(
                flt(line["contract_qty"]) * share, 2)
            doc.append("items", line)
        doc.insert()
        # The last one stays a draft: work certified, not yet issued.
        if n < len(periods):
            doc.advance_recovery_amount = round(flt(doc.gross_amount_this_period) * 0.10, 3)
            doc.save()
            doc.submit()
        made.append(doc.name)
    return made


def _variation(project, client, boq):
    from construction_management_suite.api.boq import get_boq_lines_for_variation

    doc = frappe.get_doc({
        "doctype": "Variation Order", "project": project, "company": COMPANY,
        "currency": CURRENCY, "client": client, "boq_ref": boq,
        "variation_type": "Addition", "reason": "Client Request",
        "date_raised": "2026-07-14", "time_extension_days": 21,
        "description": "Additional basement parking level requested by the employer, "
                       "including excavation, retaining wall and ramp.",
    })
    doc.append("items", {
        "nature": "Addition", "item_code": "CMS-EXCAV", "description": "Extra excavation to basement",
        "uom": "Cubic Meter", "qty": 620, "rate": 4.10, "work_category": "Civil",
    })
    doc.append("items", {
        "nature": "Addition", "item_code": "CMS-RCC-M30", "description": "Retaining wall concrete",
        "uom": "Cubic Meter", "qty": 180, "rate": 88.00, "work_category": "Civil",
    })
    doc.insert()
    doc.submit()
    return doc.name


# ───────────────────────────── subcontract ─────────────────────────────


def _subcontract(project, sub):
    doc = frappe.get_doc({
        "doctype": "Subcontract Agreement", "subcontractor": sub, "project": project,
        "company": COMPANY, "currency": CURRENCY, "contract_date": add_days(START, 30),
        "start_date": add_days(START, 45), "end_date": add_days(START, 300),
        "advance_percent": 10, "defects_liability_days": 365,
        "scope_of_work": "Supply and install 200mm hollow block walling to all floors, "
                         "including mortar, scaffolding and making good.",
    })
    doc.append("items", {"item_code": "CMS-BLK-200",
                         "description": "200mm blockwork including mortar",
                         "uom": "Square Meter", "qty": 3400, "rate": 7.90})
    doc.append("items", {"item_code": "CMS-EXT", "description": "Scaffolding hire",
                         "uom": "Nos", "qty": 1, "rate": 2400.00})
    doc.insert()
    doc.submit()
    return doc.name


def _work_order(project, sub, agreement):
    doc = frappe.get_doc({
        "doctype": "Subcontractor Work Order", "subcontractor": sub, "project": project,
        "subcontract_agreement": agreement, "company": COMPANY, "currency": CURRENCY,
        "order_date": add_days(START, 46),
    })
    doc.append("items", {"description": "Blockwork — ground and first floor",
                         "uom": "Square Meter", "contract_qty": 1700, "contract_rate": 7.90,
                         "completed_qty": 1180})
    doc.insert()
    doc.submit()
    return doc.name


def _sub_certificate(project, sub, agreement):
    doc = frappe.get_doc({
        "doctype": "Subcontractor Payment Certificate", "subcontractor": sub,
        "subcontract_agreement": agreement, "project": project, "company": COMPANY,
        "currency": CURRENCY, "submission_date": "2026-08-05",
        "retention_percent": 10,
    })
    doc.append("items", {"description": "Blockwork to 5 August 2026",
                         "amount_claimed": 9322.00})
    doc.insert()
    doc.certified_amount = 9100.00
    doc.advance_recovery = 910.00
    doc.save()
    doc.submit()
    return doc.name


# ───────────────────────────── site ─────────────────────────────


def _forecast(project, boq):
    doc = frappe.get_doc({
        "doctype": "Material Forecast", "project": project, "company": COMPANY,
        "currency": CURRENCY, "from_project": project, "boq_ref": boq,
    })
    doc.insert()
    return doc.name


def _seed_stock(warehouse):
    """Buy the materials in, so there is something to consume."""
    se = frappe.get_doc({
        "doctype": "Stock Entry", "stock_entry_type": "Material Receipt",
        "company": COMPANY, "posting_date": "2026-06-05", "set_posting_time": 1,
    })
    for code, _name, uom, rate in MATERIALS:
        se.append("items", {"item_code": code, "qty": _receipt_qty(code), "uom": uom,
                            "t_warehouse": warehouse, "basic_rate": rate})
    se.insert()
    se.submit()
    return se.name


def _receipt_qty(code):
    return {"CMS-CEMENT-OPC": 6000, "CMS-AGG-20": 700, "CMS-SAND": 400,
            "CMS-STEEL": 40000, "CMS-BLOCK-200": 30000}.get(code, 100)


def _consumption(project, warehouse):
    made = []
    for date, rows in (
        ("2026-06-28", [("CMS-CEMENT-OPC", 820), ("CMS-AGG-20", 96), ("CMS-SAND", 52)]),
        ("2026-07-30", [("CMS-CEMENT-OPC", 1150), ("CMS-AGG-20", 128), ("CMS-BLOCK-200", 6400)]),
        ("2026-08-29", [("CMS-CEMENT-OPC", 940), ("CMS-BLOCK-200", 5100), ("CMS-SAND", 44)]),
    ):
        doc = frappe.get_doc({
            "doctype": "Material Consumption Entry", "project": project, "company": COMPANY,
            "warehouse": warehouse, "posting_date": date,
        })
        for code, qty in rows:
            doc.append("items", {
                "item_code": code, "qty": qty,
                "uom": frappe.db.get_value("Item", code, "stock_uom"),
                "valuation_rate": dict((m[0], m[3]) for m in MATERIALS).get(code, 0),
            })
        doc.insert()
        doc.submit()
        made.append(doc.name)
    return made


def _site_reports(project):
    made = []
    for i, date in enumerate(("2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17")):
        doc = frappe.get_doc({
            "doctype": "Daily Site Report", "project": project, "company": COMPANY,
            "report_date": date, "weather": "Clear", "temperature": 34 + i,
            "working_hours": 9, "delays_hours": 1 if i == 2 else 0,
            "work_executed": "Blockwork to second floor; plastering started on ground floor.",
            "delays_description": "Concrete pump breakdown, two hours" if i == 2 else None,
        })
        doc.append("labour", {"trade": "Mason", "headcount": 14, "daily_rate": 12,
                              "overtime_hours": 2 if i else 0, "overtime_rate": 18})
        doc.append("labour", {"trade": "Helper", "headcount": 9, "daily_rate": 8})
        doc.append("labour", {"trade": "Steel Fixer", "headcount": 6, "daily_rate": 13})
        doc.append("equipment", {"equipment_type": "Tower crane", "hours_worked": 7,
                                 "idle_hours": 2, "hourly_rate": 14})
        doc.append("equipment", {"equipment_type": "Concrete pump", "hours_worked": 4,
                                 "idle_hours": 1 if i == 2 else 0, "hourly_rate": 22})
        doc.append("activities", {
            "activity_description": "200mm blockwork, second floor",
            "location": "Grid A-F", "planned_qty": 90, "actual_qty": 82 + i * 3,
            "uom": "Square Meter",
        })
        doc.insert()
        doc.submit()
        made.append(doc.name)
    return made
