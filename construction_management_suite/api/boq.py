"""
BOQ public API endpoints — called from JS / mobile / external integrations.
All methods are whitelisted for Frappe REST exposure.
"""

import json

import frappe
from frappe import _
from frappe.utils import flt


@frappe.whitelist()
def get_boq_summary(project):
    """Return latest approved BOQ summary for a project."""
    boqs = frappe.get_all(
        "BOQ",
        filters={"project": project, "docstatus": 1},
        fields=["name", "boq_title", "grand_total", "currency", "revision_no", "status"],
        order_by="revision_no desc",
        limit=1,
    )
    if not boqs:
        return {}
    boq = boqs[0]
    boq["items_count"] = frappe.db.count("BOQ Item", {"parent": boq["name"]})
    return boq


@frappe.whitelist()
def apply_rate_analysis_to_boq(rate_analysis, boq, item_code):
    """Push Rate Analysis rates into matching BOQ Item rows."""
    ra = frappe.get_doc("Rate Analysis", rate_analysis)
    boq_doc = frappe.get_doc("BOQ", boq)
    updated = 0
    for item in boq_doc.items:
        if item.item_code == item_code:
            output = flt(ra.output_qty) or 1
            item.rate = flt(ra.rate_per_unit)
            # Every bucket, or the component rates will not add back up to the
            # headline rate and the BOQ's cost breakdown misreports.
            item.material_rate = flt(ra.total_material_cost) / output
            item.labour_rate = flt(ra.total_labour_cost) / output
            item.equipment_rate = flt(ra.total_equipment_cost) / output
            item.subcontract_rate = flt(ra.total_subcontract_cost) / output
            item.overhead_rate = flt(ra.total_overhead_cost) / output
            item.rate_analysis_ref = rate_analysis
            item.rate_applied_on = frappe.utils.now()
            item.rate_build_up = snapshot_rate_analysis(ra)
            updated += 1
    boq_doc.calculate_item_amounts()
    boq_doc.calculate_totals()
    boq_doc.save(ignore_permissions=True)
    return _("{0} BOQ item(s) updated with rate analysis {1}").format(updated, rate_analysis)


@frappe.whitelist()
def get_project_cost_dashboard(project):
    """Return aggregated cost KPIs for a project dashboard widget."""
    budget = frappe.db.get_value(
        "Project Budget",
        {"project": project, "docstatus": 1},
        ["total_budget", "total_actual_cost", "budget_utilization_percent", "variance_amount"],
        as_dict=True,
    ) or {}

    billing = frappe.db.sql(
        """
        SELECT
            SUM(gross_amount_this_period) AS total_billed,
            SUM(net_payable_this_period) AS total_net_billed,
            COUNT(*) AS ipc_count
        FROM `tabInterim Payment Certificate`
        WHERE project = %s AND docstatus = 1
        """,
        project,
        as_dict=True,
    )[0] or {}

    subcontract = frappe.db.sql(
        """
        SELECT SUM(subcontract_value) AS total_subcontract
        FROM `tabSubcontract Agreement`
        WHERE project = %s AND docstatus = 1 AND status != 'Terminated'
        """,
        project,
        as_dict=True,
    )[0] or {}

    return {
        "budget": budget,
        "billing": billing,
        "subcontract": subcontract,
        "retention": get_retention_summary(project),
    }


@frappe.whitelist()
def get_site_progress_timeline(project, limit=30):
    """Return daily site report progress timeline for Gantt/chart display."""
    return frappe.db.sql(
        """
        SELECT report_date, cumulative_percent_complete, weather_condition, safety_incidents
        FROM `tabDaily Site Report`
        WHERE project = %s AND docstatus = 1
        ORDER BY report_date DESC
        LIMIT %s
        """,
        (project, int(limit)),
        as_dict=True,
    )


@frappe.whitelist()
def create_material_request_from_forecast(forecast_name):
    """Convert a Material Forecast into ERPNext Material Request(s)."""
    forecast = frappe.get_doc("Material Forecast", forecast_name)
    if forecast.docstatus != 1:
        frappe.throw(_("Material Forecast must be submitted first"))

    mr = frappe.new_doc("Material Request")
    mr.material_request_type = "Purchase"
    mr.company = forecast.company
    mr.transaction_date = frappe.utils.nowdate()
    mr.schedule_date = forecast.to_date or frappe.utils.add_months(mr.transaction_date, 1)

    for item in forecast.items:
        if flt(item.qty_to_order) > 0:
            mr.append("items", {
                "item_code": item.item_code,
                "qty": flt(item.qty_to_order),
                "uom": item.uom,
                "warehouse": item.warehouse,
                "project": forecast.project,
                "schedule_date": item.required_by_date or mr.schedule_date,
            })

    if not mr.items:
        frappe.msgprint(_("No items require ordering — all quantities already covered"))
        return None

    mr.insert(ignore_permissions=True)
    frappe.msgprint(_("Material Request {0} created").format(mr.name))
    return mr.name


@frappe.whitelist()
def get_retention_summary(project):
    """Return retention held vs released for a project."""
    held = frappe.db.sql(
        """
        SELECT SUM(retention_amount) AS total_held
        FROM `tabInterim Payment Certificate`
        WHERE project = %s AND docstatus = 1
        """,
        project,
        as_dict=True,
    )[0].get("total_held") or 0

    released = frappe.db.sql(
        """
        SELECT SUM(release_amount) AS total_released
        FROM `tabRetention Release`
        WHERE project = %s AND docstatus = 1
        """,
        project,
        as_dict=True,
    )[0].get("total_released") or 0

    return {
        "total_held": flt(held),
        "total_released": flt(released),
        "net_retention": flt(held) - flt(released),
    }


@frappe.whitelist()
def get_previous_ipc_position(project):
    """Where the last certificate on this project left off.

    Saves the billing officer looking up the previous certificate by hand —
    getting the cumulative figure wrong is how a client gets double-billed.
    """
    row = frappe.db.sql(
        """
        SELECT SUM(gross_amount_this_period) AS cumulative_amount,
               MAX(ipc_number) AS last_ipc_number,
               COUNT(*) AS certificates
        FROM `tabInterim Payment Certificate`
        WHERE project = %s AND docstatus = 1
        """,
        project,
        as_dict=True,
    )[0]
    return {
        "cumulative_amount": flt(row.cumulative_amount),
        "certificates": row.certificates or 0,
        "next_ipc_number": (row.last_ipc_number or 0) + 1,
    }


# ──────────────────────────── Carrying lines forward ────────────────────────────

@frappe.whitelist()
def make_cost_estimation(source_name, target_doc=None):
    """Open a Cost Estimation already carrying the BOQ's lines.

    The BOQ's component rates are costs, so they map onto the estimate's cost
    columns; the BOQ's `rate` is the selling rate and is deliberately not copied.
    """
    from frappe.model.mapper import get_mapped_doc

    def postprocess(source, target):
        target.estimation_title = _("Estimate for {0}").format(source.boq_title)
        target.boq_ref = source.name
        target.selling_price = flt(source.grand_total)

    return get_mapped_doc(
        "BOQ",
        source_name,
        {
            "BOQ": {
                "doctype": "Cost Estimation",
                "field_map": {"project": "project", "company": "company", "currency": "currency"},
                "validation": {"docstatus": ["=", 1]},
            },
            "BOQ Item": {
                "doctype": "Cost Estimation Item",
                "field_map": {
                    "item_code": "item_code",
                    "description": "description",
                    "uom": "uom",
                    "qty": "qty",
                    "material_rate": "material_cost",
                    "labour_rate": "labour_cost",
                    "equipment_rate": "equipment_cost",
                    "subcontract_rate": "subcontract_cost",
                    "overhead_rate": "overhead_cost",
                    "rate_analysis_ref": "rate_analysis_ref",
                },
            },
        },
        target_doc,
        postprocess,
    )


@frappe.whitelist()
def make_interim_payment_certificate(source_name, target_doc=None):
    """Open a certificate carrying the BOQ's lines, with each line's previously
    claimed quantity already filled in from earlier submitted certificates.

    Re-typing that figure by hand is how a client gets billed twice for the
    same work, so it is looked up rather than left blank.
    """
    from frappe.model.mapper import get_mapped_doc

    def postprocess(source, target):
        target.ipc_title = _("Payment Certificate — {0}").format(source.boq_title)
        target.boq_ref = source.name
        target.contract_value = flt(source.grand_total)

        position = get_previous_ipc_position(source.project)
        target.ipc_number = position["next_ipc_number"]
        target.previous_cumulative_amount = position["cumulative_amount"]
        if not target.retention_percent:
            target.retention_percent = flt(
                frappe.db.get_value("Project", source.project, "cms_retention_percent")
            )

        claimed = _previously_claimed_by_line(source.project)
        for row in target.items:
            row.previous_qty_claimed = flt(claimed.get(row.boq_item_ref))

    return get_mapped_doc(
        "BOQ",
        source_name,
        {
            "BOQ": {
                "doctype": "Interim Payment Certificate",
                "field_map": {
                    "project": "project",
                    "company": "company",
                    "currency": "currency",
                    "client": "client",
                },
                "validation": {"docstatus": ["=", 1]},
            },
            "BOQ Item": {
                "doctype": "IPC Item",
                "field_map": {
                    "item_code": "boq_item_ref",
                    "description": "description",
                    "uom": "uom",
                    "qty": "contract_qty",
                    "rate": "contract_rate",
                },
            },
        },
        target_doc,
        postprocess,
    )


def _previously_claimed_by_line(project):
    """Quantity already certified per BOQ line reference on this project."""
    rows = frappe.db.sql(
        """
        SELECT i.boq_item_ref AS ref, SUM(i.qty_this_period) AS qty
        FROM `tabIPC Item` i
        JOIN `tabInterim Payment Certificate` p ON p.name = i.parent
        WHERE p.project = %s AND p.docstatus = 1 AND i.boq_item_ref IS NOT NULL
        GROUP BY i.boq_item_ref
        """,
        project,
        as_dict=True,
    )
    return {r.ref: flt(r.qty) for r in rows}


# ──────────────────────────── Pricing from the rate library ────────────────────

@frappe.whitelist()
def check_rate_drift(boq):
    """Lines whose rate no longer matches the analysis they were priced from.

    A BOQ keeps its own copy of the rates, so editing an analysis afterwards
    never silently rewrites a signed bill — but it does mean the bill can fall
    out of step with the library without anyone noticing.
    """
    doc = frappe.get_doc("BOQ", boq)
    drifted = []
    for row in doc.items:
        if not row.rate_analysis_ref:
            continue
        current = flt(get_rate_analysis_rates(row.rate_analysis_ref)["rate"])
        if abs(current - flt(row.cost_rate)) > 0.005:
            drifted.append({
                "idx": row.idx,
                "item_code": row.item_code,
                "rate_analysis": row.rate_analysis_ref,
                "applied": flt(row.cost_rate),
                "current": current,
                "applied_on": row.rate_applied_on,
            })
    return drifted


@frappe.whitelist()
def get_rate_analysis_for_item(item_code):
    """The most recently updated approved analysis for an item, if there is one."""
    if not item_code:
        return None
    return frappe.db.get_value(
        "Rate Analysis",
        {"item_code": item_code, "status": "Approved"},
        "name",
        order_by="modified desc",
    )


@frappe.whitelist()
def get_rate_analysis_rates(rate_analysis):
    """The five per-unit component rates behind an analysis, plus the total."""
    ra = frappe.get_cached_doc("Rate Analysis", rate_analysis)
    output = flt(ra.output_qty) or 1
    return {
        "rate": flt(ra.rate_per_unit),
        "material_rate": flt(ra.total_material_cost) / output,
        "labour_rate": flt(ra.total_labour_cost) / output,
        "equipment_rate": flt(ra.total_equipment_cost) / output,
        "subcontract_rate": flt(ra.total_subcontract_cost) / output,
        "overhead_rate": flt(ra.total_overhead_cost) / output,
    }


@frappe.whitelist()
def price_boq_from_library(boq, overwrite=0):
    """Price every BOQ line that has an approved Rate Analysis for its item.

    Beats the old one-item-at-a-time prompt: a fifty-line bill is priced in a
    click, and by default lines already carrying a rate are left alone so a
    negotiated rate is never silently overwritten.
    """
    overwrite = int(overwrite or 0)
    doc = frappe.get_doc("BOQ", boq)
    if doc.docstatus != 0:
        frappe.throw(_("Only a draft BOQ can be priced from the library"))

    # setdefault, not a dict comprehension: iterating newest-first and assigning
    # every time leaves the OLDEST analysis in the map, which is the opposite of
    # what get_rate_analysis_for_item picks. The two must agree.
    library = {}
    for r in frappe.get_all(
        "Rate Analysis",
        filters={"status": "Approved", "item_code": ["is", "set"]},
        fields=["name", "item_code"],
        order_by="modified desc",
    ):
        library.setdefault(r.item_code, r.name)

    priced, skipped, unmatched = [], [], []
    for row in doc.items:
        analysis = library.get(row.item_code)
        if not analysis:
            unmatched.append(row.item_code)
            continue
        if flt(row.rate) and not overwrite:
            skipped.append(row.item_code)
            continue
        for field, value in get_rate_analysis_rates(analysis).items():
            row.set(field, value)
        row.rate_analysis_ref = analysis
        row.rate_applied_on = frappe.utils.now()
        row.rate_build_up = snapshot_rate_analysis(analysis)
        priced.append(row.item_code)

    if priced:
        doc.save(ignore_permissions=True)

    return {
        "priced": priced,
        "skipped": skipped,
        "unmatched": sorted(set(unmatched)),
    }


# ──────────────────────────── Where a rate comes from ─────────────────────────

@frappe.whitelist()
def get_item_rate(item_code, company=None):
    """A sensible buying rate for an item, and where it came from.

    Preference runs newest-price-first: an Item Price on the buying list is a
    deliberate current price, the last purchase rate is what you actually paid,
    and valuation is the fallback. Returning the source matters — an estimator
    should know whether a rate is quoted, historic or a book value.
    """
    if not item_code:
        return {"rate": 0, "source": None}

    price_list = frappe.db.get_single_value("Buying Settings", "buying_price_list")
    if price_list:
        price = frappe.db.get_value(
            "Item Price",
            {
                "item_code": item_code,
                "price_list": price_list,
                "buying": 1,
            },
            ["price_list_rate"],
            order_by="valid_from desc, modified desc",
        )
        if price:
            return {"rate": flt(price), "source": _("Price List: {0}").format(price_list)}

    item = frappe.db.get_value(
        "Item", item_code, ["last_purchase_rate", "valuation_rate"], as_dict=True
    ) or {}
    if flt(item.get("last_purchase_rate")):
        return {"rate": flt(item["last_purchase_rate"]), "source": _("Last Purchase Rate")}
    if flt(item.get("valuation_rate")):
        return {"rate": flt(item["valuation_rate"]), "source": _("Valuation Rate")}
    return {"rate": 0, "source": None}


@frappe.whitelist()
def get_item_valuation_rate(item_code, warehouse=None):
    """What the stock on hand is actually worth, not what it would cost to buy.

    Used where the figure values a movement rather than prices a purchase.
    """
    if not item_code:
        return {"rate": 0, "source": None}
    if warehouse:
        rate = frappe.db.get_value(
            "Bin", {"item_code": item_code, "warehouse": warehouse}, "valuation_rate"
        )
        if flt(rate):
            return {"rate": flt(rate), "source": _("Valuation in {0}").format(warehouse)}
    rate = frappe.db.get_value("Item", item_code, "valuation_rate")
    return (
        {"rate": flt(rate), "source": _("Item Valuation Rate")}
        if flt(rate)
        else {"rate": 0, "source": None}
    )


# ─────────────────── Freezing the build-up behind a priced line ───────────────

def snapshot_rate_analysis(ra):
    """A frozen, readable copy of an analysis at the moment it is applied.

    A BOQ line already keeps the five bucket rates, so a later edit to the
    library cannot move a signed bill. What it did not keep was the reasoning —
    seven cement bags at 2.100, 0.6 mason-days at 12.000 — and that is exactly
    what you have to produce when a rate is challenged two years into a job.
    Versions record the edit, but as raw diffs; this records the answer.
    """
    if isinstance(ra, str):
        ra = frappe.get_cached_doc("Rate Analysis", ra)
    return json.dumps(
        {
            "rate_analysis": ra.name,
            "analysis_name": ra.analysis_name,
            "applied_on": frappe.utils.now(),
            "output_qty": flt(ra.output_qty) or 1,
            "rate_per_unit": flt(ra.rate_per_unit),
            "buckets": {
                "Material": flt(ra.total_material_cost),
                "Labour": flt(ra.total_labour_cost),
                "Equipment": flt(ra.total_equipment_cost),
                "Subcontract": flt(ra.total_subcontract_cost),
                "Overhead": flt(ra.total_overhead_cost),
            },
            "resources": [
                {
                    "type": r.resource_type,
                    # Keep the real Item alongside the text: a take-off report has
                    # to group by item and item group, and a description cannot.
                    "resource_item": r.resource_item or None,
                    "item_group": (
                        frappe.db.get_value("Item", r.resource_item, "item_group")
                        if r.resource_item
                        else None
                    ),
                    "description": r.description or r.resource_item,
                    "uom": r.uom,
                    "qty": flt(r.qty),
                    "rate": flt(r.rate),
                    "waste_factor": flt(r.waste_factor),
                    "net_amount": flt(r.net_amount),
                }
                for r in ra.resources
            ],
        },
        default=str,
    )


@frappe.whitelist()
def get_rate_build_up(doctype, docname, idx):
    """The frozen build-up for one line, plus how the library has moved since."""
    doc = frappe.get_doc(doctype, docname)
    row = next((r for r in doc.items if str(r.idx) == str(idx)), None)
    if not row:
        frappe.throw(_("Row {0} not found").format(idx))
    if not row.get("rate_build_up"):
        if not row.get("rate_analysis_ref"):
            return {
                "frozen": None,
                "message": _("This rate was typed by hand — there is no analysis behind it. "
                             "Pick a Rate Analysis on the line to record one."),
            }
        if not frappe.db.exists("Rate Analysis", row.rate_analysis_ref):
            return {
                "frozen": None,
                "message": _("Rate Analysis {0} no longer exists, and no copy of it was kept "
                             "on this line.").format(row.rate_analysis_ref),
            }
        # Named an analysis but has no frozen copy: priced before build-ups were
        # recorded, or the two have drifted apart since. Show today's figures,
        # clearly labelled as today's — never as what this line was priced at.
        return {
            "frozen": None,
            "current": json.loads(snapshot_rate_analysis(row.rate_analysis_ref)),
            "rate_analysis": row.rate_analysis_ref,
            "line_cost_rate": flt(row.get("cost_rate")),
            "message": _("No build-up was recorded when this line was priced, so what follows is "
                         "{0} as it stands today — not necessarily what this rate came from.").format(
                             row.rate_analysis_ref),
        }

    frozen = json.loads(row.rate_build_up)
    current = None
    if row.get("rate_analysis_ref") and frappe.db.exists("Rate Analysis", row.rate_analysis_ref):
        current = json.loads(snapshot_rate_analysis(row.rate_analysis_ref))
    return {"frozen": frozen, "current": current}
