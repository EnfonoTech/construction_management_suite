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
        WHERE project = %(project)s AND docstatus = 1
        """,
        {"project": project},
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
        WHERE project = %(project)s AND docstatus = 1
        """,
        {"project": project},
        as_dict=True,
    )[0].get("total_held") or 0

    released = frappe.db.sql(
        """
        SELECT SUM(release_amount) AS total_released
        FROM `tabRetention Release`
        WHERE project = %(project)s AND docstatus = 1
        """,
        {"project": project},
        as_dict=True,
    )[0].get("total_released") or 0

    return {
        "total_held": flt(held),
        "total_released": flt(released),
        "net_retention": flt(held) - flt(released),
    }


@frappe.whitelist()
def get_previous_ipc_position(project, exclude_ipc=None):
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
        WHERE project = %(project)s AND docstatus = 1 AND name != %(exclude)s
        """,
        {"project": project, "exclude": exclude_ipc or ""},
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
        _own_naming_series(target)
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

    if not frappe.db.get_value("BOQ", source_name, "project"):
        frappe.throw(
            _("{0} has no Project. A certificate is paid against a project — "
              "use <b>Create &gt; Project</b> on the BOQ first.").format(source_name)
        )

    def postprocess(source, target):
        _own_naming_series(target)
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
        # Nothing left to certify on a line is not worth a row on the
        # certificate; the engineer should see only live work.
        target.items = [
            r for r in target.items
            if flt(r.contract_qty) - flt(r.previous_qty_claimed) > 0.0001
        ] or target.items

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
                    # The BOQ Item ROW name, not the item code. An item can
                    # appear on two lines of one bill in different sections; only
                    # the row identifies which of them is being certified.
                    "name": "boq_item_ref",
                    "item_code": "item_code",
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


def _own_naming_series(target):
    """Keep the target's own series.

    get_mapped_doc copies every field the two doctypes share, and
    `naming_series` is one of them — so a Cost Estimation raised from a BOQ came
    out named BOQ-2026-0009.
    """
    meta = frappe.get_meta(target.doctype)
    field = meta.get_field("naming_series")
    if field and field.options:
        target.naming_series = field.options.split("\n")[0]


def _previously_claimed_by_line(project, exclude_ipc=None):
    """Quantity already certified per BOQ line on this project.

    Submitted certificates only — a draft is a proposal, and counting it would
    block the very certificate being drafted from claiming its own quantity.
    """
    rows = frappe.db.sql(
        """
        SELECT i.boq_item_ref AS ref, SUM(i.qty_this_period) AS qty
        FROM `tabIPC Item` i
        JOIN `tabInterim Payment Certificate` p ON p.name = i.parent
        WHERE p.project = %(project)s AND p.docstatus = 1
          AND i.boq_item_ref IS NOT NULL AND i.boq_item_ref != ''
          AND p.name != %(exclude)s
        GROUP BY i.boq_item_ref
        """,
        {"project": project, "exclude": exclude_ipc or ""},
        as_dict=True,
    )
    return {r.ref: flt(r.qty) for r in rows}


@frappe.whitelist()
def get_agreement_lines(agreement, work_order=None):
    """The agreed scope, with how much each line has been instructed elsewhere.

    Quantities already on the order being edited are NOT netted off here: the
    form sends its in-memory document, so the caller knows what is on screen and
    the database does not. Reading them back here meant reducing a pulled
    quantity and pulling again reported the agreement as fully instructed.
    """
    doc = frappe.get_doc("Subcontract Agreement", agreement)
    if doc.docstatus != 1:
        frappe.throw(_("Only a signed agreement can be released to a work order"))

    # Submitted orders only — a draft is a proposal, not an instruction. Counted
    # by row, falling back to the description for orders written before the
    # reference existed, whose quantity would otherwise be invisible.
    instructed = frappe.db.sql(
        """
        SELECT i.agreement_item_ref AS ref, i.description AS d,
               SUM(i.contract_qty) AS qty, GROUP_CONCAT(DISTINCT w.name) AS orders
        FROM `tabSubcontractor Work Order Item` i
        JOIN `tabSubcontractor Work Order` w ON w.name = i.parent
        WHERE w.subcontract_agreement = %(a)s AND w.docstatus = 1 AND w.name != %(w)s
        GROUP BY i.agreement_item_ref, i.description
        """,
        {"a": agreement, "w": work_order or ""},
        as_dict=True,
    )
    done, by_text, where = {}, {}, {}
    for r in instructed:
        key = r.ref or None
        if key:
            done[key] = done.get(key, 0) + flt(r.qty)
            where.setdefault(key, set()).update((r.orders or "").split(","))
        else:
            by_text[r.d] = by_text.get(r.d, 0) + flt(r.qty)

    lines = []
    for row in doc.items:
        elsewhere = flt(done.get(row.name)) + flt(by_text.get(row.description))
        lines.append({
            "agreement_item_ref": row.name,
            "item_code": row.item_code,
            "boq_ref": row.boq_ref,
            "boq_item_no": row.boq_item_no,
            "description": row.description,
            "uom": row.uom,
            "agreed_qty": flt(row.qty),
            "instructed_elsewhere": elsewhere,
            "contract_rate": flt(row.rate),
            "completed_qty": 0,
            "instructed_on": sorted(o for o in where.get(row.name, set()) if o),
        })
    return lines


@frappe.whitelist()
def get_completed_work(agreement, certificate=None):
    """What the work orders say is built, less what has already been claimed.

    The certificate used to be typed from scratch, so a subcontractor could be
    paid for work no order records as complete.
    """
    rows = frappe.db.sql(
        """
        SELECT i.name AS ref, i.description AS d, i.uom AS uom, i.item_code AS item_code,
               i.boq_ref AS boq_ref, i.boq_item_no AS boq_item_no,
               i.contract_rate AS rate, i.completed_qty AS done
        FROM `tabSubcontractor Work Order Item` i
        JOIN `tabSubcontractor Work Order` w ON w.name = i.parent
        WHERE w.subcontract_agreement = %s AND w.docstatus = 1
        """,
        agreement,
        as_dict=True,
    )
    claimed = frappe.db.sql(
        """
        SELECT i.work_order_item_ref AS d, SUM(i.qty_completed) AS qty
        FROM `tabSubcontractor Payment Item` i
        JOIN `tabSubcontractor Payment Certificate` c ON c.name = i.parent
        WHERE c.subcontract_agreement = %(a)s AND c.docstatus = 1 AND c.name != %(c)s
          AND i.work_order_item_ref IS NOT NULL AND i.work_order_item_ref != ''
        GROUP BY i.work_order_item_ref
        """,
        {"a": agreement, "c": certificate or ""},
        as_dict=True,
    )
    already = {r.d: flt(r.qty) for r in claimed}

    lines = []
    for r in rows:
        outstanding = flt(r.done) - flt(already.get(r.ref))
        if outstanding <= 0.0001:
            continue
        lines.append({
            "work_order_item_ref": r.ref,
            "item_code": r.item_code,
            "boq_ref": r.boq_ref,
            "boq_item_no": r.boq_item_no,
            "description": r.d,
            "uom": r.uom,
            "qty_completed": outstanding,
            "contract_rate": flt(r.rate),
            "amount_claimed": outstanding * flt(r.rate),
        })
    return lines


@frappe.whitelist()
def get_boq_lines_for_subcontract(boq):
    """Contract lines a trade could be engaged to deliver.

    Carries the line's COST rate, not its selling rate — what a subcontract
    should be judged against is what the bill was priced to build the work for,
    never what the client is being charged.
    """
    doc = frappe.get_doc("BOQ", boq)
    if doc.docstatus != 1:
        frappe.throw(_("Only a submitted BOQ can be subcontracted against"))
    return [
        {
            "boq_ref": row.name,
            "boq_item_no": row.item_no,
            "boq_cost_rate": flt(row.cost_rate),
            "item_code": row.item_code,
            "description": row.description or row.item_code,
            "uom": row.uom,
            "qty": flt(row.qty),
            "rate": 0,
        }
        for row in doc.items
    ]


@frappe.whitelist()
def get_boq_lines_for_variation(boq):
    """Every line of a bill, so a variation can be built against the real rows.

    A variation usually omits or re-rates work already in the contract, and the
    row it refers to is what ties the two together — `boq_item_ref` held free
    text before, which identified nothing.
    """
    doc = frappe.get_doc("BOQ", boq)
    if doc.docstatus != 1:
        frappe.throw(_("Only a submitted BOQ can be varied"))
    return [
        {
            "boq_item_ref": row.name,
            "item_no": row.item_no,
            "item_code": row.item_code,
            "description": row.description or row.item_code,
            "uom": row.uom,
            "qty": flt(row.qty),
            "rate": flt(row.rate),
            "work_category": row.work_category,
        }
        for row in doc.items
    ]


@frappe.whitelist()
def get_boq_lines_for_ipc(boq, ipc=None):
    """BOQ lines with work still left to certify, ready to append to a certificate."""
    doc = frappe.get_doc("BOQ", boq)
    if doc.docstatus != 1:
        frappe.throw(_("Only a submitted BOQ can be certified against"))

    claimed = _previously_claimed_by_line(doc.project, exclude_ipc=ipc)
    lines = []
    for row in doc.items:
        previous = flt(claimed.get(row.name))
        remaining = flt(row.qty) - previous
        if remaining <= 0.0001:
            continue
        lines.append({
            "boq_item_ref": row.name,
            "item_code": row.item_code,
            "description": row.description or row.item_code,
            "uom": row.uom,
            "contract_qty": flt(row.qty),
            "contract_rate": flt(row.rate),
            "previous_qty_claimed": previous,
            "qty_this_period": 0,
        })
    return lines


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
    """The analysis an item is priced from when nobody picks one by hand.

    An item can legitimately carry several approved analyses — different
    specifications, different sites, a tender version alongside the contract
    one. `is_default` is how the estimator says which is the real one; without
    it the choice fell to whichever happened to be saved last, which is not a
    decision anybody made.
    """
    if not item_code:
        return None
    return frappe.db.get_value(
        "Rate Analysis",
        {"item_code": item_code, "status": "Approved", "is_active": 1},
        "name",
        order_by="is_default desc, modified desc",
    )


@frappe.whitelist()
def get_selling_rate(item_code, price_list):
    """The agreed selling rate for an item, if the list carries one."""
    if not item_code or not price_list:
        return 0
    return flt(
        frappe.db.get_value(
            "Item Price",
            {"item_code": item_code, "price_list": price_list, "selling": 1},
            "price_list_rate",
            order_by="valid_from desc, modified desc",
        )
    )


@frappe.whitelist()
def get_boq_line_rates(item_code, rate_source=None, price_list=None):
    """Everything a BOQ line needs the moment its item is chosen.

    One call, and one place that decides where the SELLING rate comes from —
    the row handler used to take it from the rate analysis whatever the bill's
    Rate Source said, so a bill priced against a client's schedule of rates
    silently billed them at cost.

    The cost breakdown is always returned when an analysis exists, whichever
    source is in force: cost is what margin is measured against, and a line
    priced from a price list still needs to know what it costs to build.
    """
    out = {
        "rate_analysis_ref": None,
        "material_rate": 0,
        "labour_rate": 0,
        "equipment_rate": 0,
        "subcontract_rate": 0,
        "overhead_rate": 0,
    }
    if not item_code:
        return out

    analysis = get_rate_analysis_for_item(item_code)
    cost = 0
    if analysis:
        rates = get_rate_analysis_rates(analysis)
        cost = flt(rates.pop("rate"))
        out.update(rates)
        out["rate_analysis_ref"] = analysis

    source = rate_source or "Rate Analysis"
    if source == "Rate Analysis" and analysis:
        out["rate"] = cost
    elif source == "Price List":
        rate = get_selling_rate(item_code, price_list)
        if rate:
            out["rate"] = rate
    # Manual returns no rate at all — that is what Manual means.
    return out


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
def price_boq_from_library(boq, overwrite=0, source=None, price_list=None):
    """Price every line of a BOQ from the library, or from a price list.

    Beats the old one-item-at-a-time prompt: a fifty-line bill is priced in a
    click, and by default lines already carrying a rate are left alone so a
    negotiated rate is never silently overwritten.

    Two sources, because they answer different questions. A **Rate Analysis**
    builds the rate from what the work consumes, and brings the cost breakdown
    and a frozen build-up with it — the only source that can support a take-off
    or a margin. A **Price List** is a rate somebody already agreed: a client's
    schedule of rates, a framework agreement, last year's tender. It sets the
    selling rate and nothing else, so margin stays unknown unless the line also
    carries a build-up.
    """
    from construction_management_suite.utils.settings import cms_setting

    overwrite = int(overwrite or 0)
    doc = frappe.get_doc("BOQ", boq)
    if doc.docstatus != 0:
        frappe.throw(_("Only a draft BOQ can be priced from the library"))

    source = source or doc.rate_source or cms_setting("default_rate_source", "Rate Analysis")
    if source == "Price List":
        return _price_boq_from_price_list(
            doc,
            overwrite,
            price_list or doc.selling_price_list or cms_setting("default_selling_price_list"),
        )

    # setdefault, not a dict comprehension: iterating best-first and assigning
    # every time leaves the WORST analysis in the map, which is the opposite of
    # what get_rate_analysis_for_item picks. The two must agree, so the filters
    # and the ordering here are that function's, applied in bulk.
    library = {}
    for r in frappe.get_all(
        "Rate Analysis",
        filters={"status": "Approved", "is_active": 1, "item_code": ["is", "set"]},
        fields=["name", "item_code"],
        order_by="is_default desc, modified desc",
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
        "source": _("Rate Analysis"),
        "priced": priced,
        "skipped": skipped,
        "unmatched": sorted(set(unmatched)),
    }


def _price_boq_from_price_list(doc, overwrite, price_list):
    """Set each line's selling rate from an Item Price in the chosen list.

    Only `rate` is touched. The cost breakdown is left exactly as it is, so a
    line already costed from an analysis keeps its build-up and simply gets a
    different selling rate — which is the normal case when a client hands you
    their own schedule of rates to price against.
    """
    if not price_list:
        frappe.throw(_("Choose a Price List, or set a default in Construction Settings"))
    if not frappe.db.get_value("Price List", price_list, "selling"):
        frappe.throw(_("{0} is not a selling price list").format(price_list))

    prices = {
        r.item_code: flt(r.price_list_rate)
        for r in frappe.get_all(
            "Item Price",
            filters={"price_list": price_list, "selling": 1},
            fields=["item_code", "price_list_rate"],
            order_by="valid_from asc, modified asc",
        )
    }

    priced, skipped, unmatched = [], [], []
    for row in doc.items:
        rate = prices.get(row.item_code)
        if not rate:
            unmatched.append(row.item_code)
            continue
        if flt(row.rate) and not overwrite:
            skipped.append(row.item_code)
            continue
        row.rate = rate
        row.rate_applied_on = frappe.utils.now()
        priced.append(row.item_code)

    if priced:
        doc.save(ignore_permissions=True)

    return {
        "source": _("Price List: {0}").format(price_list),
        "priced": priced,
        "skipped": skipped,
        "unmatched": sorted(set(unmatched)),
    }


# ──────────────────────────── Where a rate comes from ─────────────────────────

def _rate_from_basis(item_code, basis, price_list=None):
    """Resolve exactly one source. No fallback — see get_item_rate."""
    if basis == "Manual":
        return {"rate": 0, "source": None}

    if basis in ("Valuation Rate", "Last Purchase Rate"):
        field = "valuation_rate" if basis == "Valuation Rate" else "last_purchase_rate"
        rate = flt(frappe.db.get_value("Item", item_code, field))
        return {"rate": rate, "source": _(basis)} if rate else {"rate": 0, "source": None}

    if basis == "Price List":
        price_list = price_list or frappe.db.get_single_value(
            "Buying Settings", "buying_price_list"
        )
        if not price_list:
            return {"rate": 0, "source": None}
        rate = frappe.db.get_value(
            "Item Price",
            {"item_code": item_code, "price_list": price_list, "buying": 1},
            "price_list_rate",
            order_by="valid_from desc, modified desc",
        )
        return (
            {"rate": flt(rate), "source": _("Price List: {0}").format(price_list)}
            if flt(rate)
            else {"rate": 0, "source": None}
        )

    # A typo'd basis silently falling through to the waterfall would quietly
    # write a price-list rate where a valuation was asked for.
    frappe.throw(_("Unknown rate basis {0}").format(basis))


@frappe.whitelist()
def get_item_rate(item_code, company=None, basis=None, price_list=None):
    """A sensible buying rate for an item, and where it came from.

    With no `basis`, preference runs newest-price-first: an Item Price on the
    buying list is a deliberate current price, the last purchase rate is what
    you actually paid, and valuation is the fallback. Returning the source
    matters — an estimator should know whether a rate is quoted, historic or a
    book value.

    With an explicit `basis` only that source is consulted, and a miss returns
    zero rather than falling back, so the caller can report it instead of
    writing a worse number over a good one.
    """
    if not item_code:
        return {"rate": 0, "source": None}

    if basis:
        return _rate_from_basis(item_code, basis, price_list)

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
