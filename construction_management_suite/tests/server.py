import os
import json, frappe
frappe.init(site="misk"); frappe.connect(); frappe.set_user("Administrator")

CHILD = {
 "BOQ": "items", "Rate Analysis": "resources", "Cost Estimation": "items",
 "Interim Payment Certificate": "items", "Variation Order": "items",
 "Subcontract Agreement": "items", "Subcontractor Payment Certificate": "items",
 "Subcontractor Work Order": "items", "Material Forecast": "items",
 "Material Consumption Entry": "items", "Project Budget": "items",
}
# Which controller method does the pure arithmetic (skipping DB-dependent parts).
CALC = {
 "BOQ": lambda d: (d.calculate_item_amounts(), d.calculate_totals()),
 "Rate Analysis": lambda d: (d.calculate_resources(), d.calculate_totals()),
 "Cost Estimation": lambda d: (d.pull_costs_from_rate_analysis(), d.calculate_totals()),
 "Interim Payment Certificate": lambda d: (d.calculate_items(), d._deduct()),
 "Variation Order": lambda d: (d.calculate_items(), d.calculate_totals()),
 "Subcontract Agreement": lambda d: (d.calculate_items(), d.calculate_advance()),
 "Subcontractor Payment Certificate": lambda d: d.calculate_totals(),
 "Subcontractor Work Order": lambda d: d.calculate_totals(),
 "Material Forecast": lambda d: d.recalculate(),
 "Material Consumption Entry": lambda d: [
     setattr(r, "amount", frappe.utils.flt(r.qty) * frappe.utils.flt(r.valuation_rate)) for r in d.items],
 "Project Budget": lambda d: d.calculate_variance(),
 "Daily Site Report": lambda d: (d.calculate_labour_cost(), d.calculate_equipment_cost()),
}

cases = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cases.json')))
out = {}
for dt, data in cases.items():
    doc = frappe.new_doc(dt)
    for k, v in data.items():
        if isinstance(v, list):
            for row in v:
                doc.append(k, dict(row))
        else:
            doc.set(k, v)
    # IPC's deduction step reads previous certificates from the DB; isolate the
    # arithmetic so the comparison is like-for-like.
    if dt == "Interim Payment Certificate":
        doc._deduct = lambda: (
            setattr(doc, "retention_amount",
                    frappe.utils.flt(doc.gross_amount_this_period) * frappe.utils.flt(doc.retention_percent) / 100),
            setattr(doc, "net_payable_this_period",
                    frappe.utils.flt(doc.gross_amount_this_period)
                    - frappe.utils.flt(doc.retention_amount)
                    - frappe.utils.flt(doc.advance_recovery_amount)
                    - frappe.utils.flt(doc.other_deductions)))
    CALC[dt](doc)
    d = doc.as_dict()
    rec = {k: v for k, v in d.items() if isinstance(v, (int, float)) and k not in ("docstatus", "idx")}
    tables = {}
    for tf in ({CHILD.get(dt)} | {"labour", "equipment"}):
        if tf and d.get(tf):
            tables[tf] = [{k: v for k, v in r.items()
                           if isinstance(v, (int, float)) and k not in ("docstatus", "idx")} for r in d[tf]]
    rec["_tables"] = tables
    out[dt] = rec
print(json.dumps(out, default=str))
