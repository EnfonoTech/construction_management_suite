"""
BOQ public API endpoints — called from JS / mobile / external integrations.
All methods are whitelisted for Frappe REST exposure.
"""

import json

import frappe
from frappe import _
from frappe.utils import flt

from construction_management_suite.utils.settings import company_scoped_rates


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
    if ra.company and boq_doc.company != ra.company and company_scoped_rates():
        frappe.throw(
            _("{0} belongs to {1}; this bill is for {2}.").format(
                rate_analysis, ra.company, boq_doc.company
            )
        )
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
def take_off_outstanding(project):
    """What the priced work still needs bought, per material per line of work.

    A forecast is optional — a job can be bought straight off the estimate —
    and without one nothing filled the work reference on a request, so what
    was needed and what was bought never met in a report. This is the same
    figure a forecast would show, read live instead of planned.
    """
    from construction_management_suite.material_planning.doctype.material_consumption_entry.material_consumption_entry import (
        take_off_by_line,
    )

    if not project:
        frappe.throw(_("Choose a project"))

    asked = {}
    for table, parent, cond in (
        ("Material Request Item", "Material Request", "m.docstatus = 1 AND m.status NOT IN ('Stopped','Cancelled')"),
        ("Purchase Order Item", "Purchase Order", "m.docstatus = 1 AND m.status NOT IN ('Closed','Cancelled')"),
    ):
        for row in frappe.db.sql(
            f"""
            SELECT i.item_code AS code, i.cms_work_item AS work, SUM(i.qty) AS qty
            FROM `tab{table}` i JOIN `tab{parent}` m ON m.name = i.parent
            WHERE i.project = %s AND {cond}
            GROUP BY i.item_code, i.cms_work_item
            """,
            project,
            as_dict=True,
        ):
            # A request converted to an order would otherwise count twice, so
            # only the larger of the two is taken per material and work.
            key = (row.code, row.work or None)
            asked[key] = max(flt(asked.get(key)), flt(row.qty))

    from construction_management_suite.material_planning.doctype.material_consumption_entry.material_consumption_entry import (
        work_no_map,
    )

    from construction_management_suite.utils.billing import orderable_qty

    numbers = work_no_map(project)
    out = []
    for (code, work_item), entry in take_off_by_line(project).items():
        outstanding = flt(entry["qty"]) - flt(asked.get((code, work_item)))
        if outstanding <= 0.0001:
            continue
        out.append({
            "item_code": code,
            "uom": entry["uom"],
            # ERPNext refuses a fraction on a whole-number UOM outright, so what
            # is offered has to be orderable. Same helper the forecast uses.
            "qty": orderable_qty(outstanding, entry["uom"]),
            "cms_work_item": work_item,
            "work_no": numbers.get(work_item),
        })
    return sorted(out, key=lambda r: (str(r["work_no"] or ""), str(r["cms_work_item"] or ""), r["item_code"]))


@frappe.whitelist()
def create_material_request_from_forecast(forecast_name):
    """Convert a Material Forecast into ERPNext Material Request(s)."""
    forecast = frappe.get_doc("Material Forecast", forecast_name)
    if forecast.docstatus != 1:
        frappe.throw(_("Material Forecast must be submitted first"))
    if forecast.material_request_ref and frappe.db.exists(
        "Material Request", forecast.material_request_ref
    ):
        # Nothing stopped a second one, and the coverage figure only counted
        # purchase orders, so the same quantity was offered again.
        frappe.throw(
            _("This forecast already raised {0}. Cancel it before raising another.")
            .format(frappe.utils.get_link_to_form("Material Request", forecast.material_request_ref)),
            title=_("Already requested"),
        )

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
                # Rides on to the order, the receipt and the invoice by itself:
                # frappe's mapper copies fields of the same name.
                "cms_work_item": item.work_item,
            })

    from construction_management_suite.utils.accounting import get_cost_center
    from construction_management_suite.utils.billing import stamp_accounting

    stamp_accounting(mr, project=forecast.project,
                     cost_center=get_cost_center(forecast.project, forecast.company))

    if not mr.items:
        frappe.msgprint(_("No items require ordering — all quantities already covered"))
        return None

    mr.cms_forecast_ref = forecast.name
    mr.insert(ignore_permissions=True)
    mr.submit()
    frappe.db.set_value("Material Forecast", forecast.name, "material_request_ref", mr.name,
                        update_modified=False)
    frappe.msgprint(_("Material Request {0} submitted").format(mr.name))
    return mr.name


@frappe.whitelist()
def get_consumption_position(project):
    """What a project has consumed against what its bills were priced to use."""
    from construction_management_suite.material_planning.doctype.material_consumption_entry.material_consumption_entry import (
        consumed_by_item,
        take_off_detail,
    )

    detail = {d["item_code"]: d for d in take_off_detail(project)}
    used = consumed_by_item(project)
    allowed_qty = sum(flt(d["boq_qty"]) for d in detail.values())
    allowed_value = sum(
        flt(d["boq_qty"]) * flt(d["estimated_rate"])
        for d in detail.values()
    )
    used_value = sum(
        flt(q) * flt((detail.get(code) or {}).get("estimated_rate")) for code, q in used.items()
    )
    return {
        "allowed": allowed_qty,
        "used": sum(flt(q) for q in used.values()),
        "allowed_value": allowed_value,
        "used_value": used_value,
    }


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
            row.previous_qty_claimed = flt(claimed.get(row.item_code))
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
                    # The work Item is the key. One line per work item per bill
                    # is enforced, and unlike a child-row name the item code
                    # survives the bill being revised — which is what let a
                    # revision reset claimed-to-date to zero.
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
        SELECT i.item_code AS ref, SUM(i.qty_this_period) AS qty
        FROM `tabIPC Item` i
        JOIN `tabInterim Payment Certificate` p ON p.name = i.parent
        WHERE p.project = %(project)s AND p.docstatus = 1
          AND i.item_code IS NOT NULL AND i.item_code != ''
          AND p.name != %(exclude)s
        GROUP BY i.item_code
        """,
        {"project": project, "exclude": exclude_ipc or ""},
        as_dict=True,
    )
    return {r.ref: flt(r.qty) for r in rows}


@frappe.whitelist()
def get_agreement_lines(agreement, work_order=None):
    """The agreed scope, with how much of each line another order has delivered.

    Quantities already on the order being edited are NOT netted off here: the
    form sends its in-memory document, so the caller knows what is on screen and
    the database does not. Reading them back here meant reducing a pulled
    quantity and pulling again reported the agreement as fully instructed.
    """
    doc = frappe.get_doc("Subcontract Agreement", agreement)
    if doc.docstatus != 1:
        frappe.throw(_("Only a signed agreement can be released to a work order"))

    # Submitted orders only — a draft is a proposal, not an instruction. Counted
    # by work Item: one line per work item per document is enforced, so the item
    # code identifies the agreement line an order released.
    instructed = frappe.db.sql(
        """
        SELECT i.item_code AS ref, i.description AS d, w.name AS wo,
               w.status AS status, i.contract_qty AS qty, i.completed_qty AS done
        FROM `tabSubcontractor Work Order Item` i
        JOIN `tabSubcontractor Work Order` w ON w.name = i.parent
        WHERE w.subcontract_agreement = %(a)s AND w.docstatus = 1 AND w.name != %(w)s
        """,
        {"a": agreement, "w": work_order or ""},
        as_dict=True,
    )
    # What an order still HOLDS is what it has not delivered. An order that is
    # open and half built is not a reason to refuse the rest of the scope — the
    # balance may go to another trade, or in another batch — so the undelivered
    # quantity is offered and the orders already holding it are named, rather
    # than the decision being made here.
    delivered, where = {}, {}
    for r in instructed:
        key = r.ref or None
        built = flt(r.done)
        if not key:
            continue
        delivered[key] = delivered.get(key, 0) + built
        if flt(r.qty) - built > 0.0001:
            where.setdefault(key, {})[r.wo] = {
                "instructed": flt(r.qty), "completed": built, "status": r.status,
            }

    lines = []
    for row in doc.items:
        elsewhere = flt(delivered.get(row.item_code))
        lines.append({
            "item_code": row.item_code,
            "work_no": row.work_no,
            "description": row.description,
            "uom": row.uom,
            "agreed_qty": flt(row.qty),
            # Named for what it is: quantity another order has DELIVERED and
            # which therefore cannot be instructed again.
            "delivered_elsewhere": elsewhere,
            "contract_rate": flt(row.rate),
            "completed_qty": 0,
            "open_on": [
                dict(order=k, **v)
                for k, v in sorted((where.get(row.item_code) or {}).items())
            ],
        })
    return lines


@frappe.whitelist()
def get_completed_work(agreement, certificate=None, work_order=None):
    """What the work orders say is built, less what has already been claimed.

    The certificate used to be typed from scratch, so a subcontractor could be
    paid for work no order records as complete.
    """
    rows = frappe.db.sql(
        """
        SELECT i.item_code AS ref, i.description AS d, i.uom AS uom,
               i.item_code AS item_code, i.work_no AS work_no,
               i.contract_rate AS rate, i.completed_qty AS done
        FROM `tabSubcontractor Work Order Item` i
        JOIN `tabSubcontractor Work Order` w ON w.name = i.parent
        WHERE w.subcontract_agreement = %(a)s AND w.docstatus = 1
          AND (%(w)s = '' OR w.name = %(w)s)
        """,
        {"a": agreement, "w": work_order or ""},
        as_dict=True,
    )
    claimed = frappe.db.sql(
        """
        SELECT i.item_code AS d, SUM(i.qty_completed) AS qty
        FROM `tabSubcontractor Payment Item` i
        JOIN `tabSubcontractor Payment Certificate` c ON c.name = i.parent
        WHERE c.subcontract_agreement = %(a)s AND c.docstatus = 1 AND c.name != %(c)s
          AND i.item_code IS NOT NULL AND i.item_code != ''
        GROUP BY i.item_code
        """,
        {"a": agreement, "c": certificate or ""},
        as_dict=True,
    )
    already = {r.d: flt(r.qty) for r in claimed}

    # Several orders may release the same work item, so the built quantity is
    # summed per item before the claim is netted off it.
    #
    # This summed the FIRST order twice. `setdefault` stored a copy of the row,
    # so `e is not r` was true even for the row that had just created the
    # entry, and its quantity was added to itself: a single order for 12 came
    # out as 24. It survived on the sample data because the second certificate
    # netted off the first, and 24 less 12 already claimed reads as right.
    built = {}
    for r in rows:
        entry = built.get(r.ref)
        if entry is None:
            built[r.ref] = dict(r)
        else:
            entry["done"] = flt(entry["done"]) + flt(r.done)

    lines = []
    for r in built.values():
        r = frappe._dict(r)
        outstanding = flt(r.done) - flt(already.get(r.ref))
        if outstanding <= 0.0001:
            continue
        lines.append({
            "item_code": r.item_code,
            "work_no": r.work_no,
            "description": r.d,
            "uom": r.uom,
            "qty_completed": outstanding,
            "contract_rate": flt(r.rate),
            "amount_claimed": outstanding * flt(r.rate),
        })
    return lines


@frappe.whitelist()
def make_payment_certificate(source_name, target_doc=None):
    """Open a certificate carrying what a work order has actually built.

    The button used to hand over three header fields and leave the schedule to
    be retyped from the order sitting on the next screen — which is how a
    subcontractor gets paid for a quantity no order records.
    """
    from frappe.model.mapper import get_mapped_doc

    def postprocess(source, target):
        from construction_management_suite.utils.billing import carry_taxes

        # get_mapped_doc copies every field the two doctypes share, naming_series
        # included — so a certificate raised from a work order came out named
        # SWO-2026-0005 and burnt a number from the order's series.
        _own_naming_series(target)
        agreement = frappe.get_doc("Subcontract Agreement", source.subcontract_agreement)
        target.retention_percent = flt(agreement.retention_percent)
        target.submission_date = frappe.utils.nowdate()

        # The tax the work was let at is the tax it is certified at. Without
        # this the certificate arrived untaxed and the invoice behind it too,
        # while the agreement plainly said 5%.
        carry_taxes(agreement, target)

        # The period this certificate covers: from the day after the last one
        # on the same agreement, or the order's own start, to today. Both were
        # left blank, on a document whose whole purpose is to say what was
        # built between two dates.
        last = frappe.db.get_value(
            "Subcontractor Payment Certificate",
            {"subcontract_agreement": agreement.name, "docstatus": 1},
            "period_to",
            order_by="period_to desc",
        )
        target.period_to = frappe.utils.nowdate()
        target.period_from = (
            frappe.utils.add_days(last, 1) if last else source.get("start_date")
        )
        # A second certificate on the same day would otherwise start tomorrow
        # and end today. One day is a short period; a backwards one is not a
        # period at all.
        if target.period_from and frappe.utils.getdate(target.period_from) > frappe.utils.getdate(target.period_to):
            target.period_from = target.period_to

        lines = get_completed_work(source.subcontract_agreement, work_order=source.name)
        if not lines:
            frappe.throw(
                _("Everything built on {0} has already been certified. Record more "
                  "progress on it first.").format(source.name),
                title=_("Nothing to certify"),
            )
        for line in lines:
            # Which order this was built under. The field existed on the line
            # and nothing ever filled it, so a certificate could not say where
            # its quantities came from.
            line["work_order_ref"] = source.name
            target.append("items", line)

    return get_mapped_doc(
        "Subcontractor Work Order",
        source_name,
        {
            "Subcontractor Work Order": {
                "doctype": "Subcontractor Payment Certificate",
                "field_map": {
                    "subcontract_agreement": "subcontract_agreement",
                    "project": "project",
                    "subcontractor": "subcontractor",
                    "company": "company",
                    "currency": "currency",
                },
                "validation": {"docstatus": ["=", 1]},
            },
        },
        target_doc,
        postprocess,
    )


@frappe.whitelist()
def get_work_lines_for_subcontract(project):
    """Lines of work a trade could be engaged to deliver.

    Read from the **Cost Estimation**, not the bill. A subcontract is let
    against what the work is planned to cost, never against the marked-up rate
    the client is charged — judging a quote by the selling rate makes every
    subcontract look cheap.
    """
    from construction_management_suite.material_planning.doctype.material_consumption_entry.material_consumption_entry import (
        work_no_map,
    )
    from construction_management_suite.utils.validations import current_estimate

    estimate = current_estimate(project)
    if not estimate:
        frappe.throw(
            _("{0} has no submitted Cost Estimation to subcontract against.").format(
                project
            )
        )
    doc = frappe.get_doc("Cost Estimation", estimate)
    numbers = work_no_map(project)
    return [
        {
            "item_code": row.item_code,
            "work_no": numbers.get(row.item_code),
            "boq_cost_rate": flt(row.unit_cost),
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
    work Item is what ties the two together.
    """
    doc = frappe.get_doc("BOQ", boq)
    if doc.docstatus != 1:
        frappe.throw(_("Only a submitted BOQ can be varied"))
    return [
        {
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
        previous = flt(claimed.get(row.item_code))
        remaining = flt(row.qty) - previous
        if remaining <= 0.0001:
            continue
        lines.append({
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
def get_rate_analysis_for_item(item_code, company=None):
    """The analysis an item is priced from when nobody picks one by hand.

    An item can legitimately carry several approved analyses — different
    specifications, different sites, a tender version alongside the contract
    one. `is_default` is how the estimator says which is the real one; without
    it the choice fell to whichever happened to be saved last, which is not a
    decision anybody made.

    The search is confined to the company being priced for unless the site has
    turned that off; see `company_scoped_rates` for why.
    """
    if not item_code:
        return None
    filters = {"item_code": item_code, "status": "Approved", "is_active": 1}
    if company and company_scoped_rates():
        filters["company"] = company
    return frappe.db.get_value(
        "Rate Analysis",
        filters,
        "name",
        order_by="is_default desc, modified desc",
    )


@frappe.whitelist()
def rate_library_is_company_scoped():
    """For the form's Rate Analysis picker, which cannot read the Single.

    Construction Settings is readable by System Manager and Projects Manager
    only, so a site user filling a bill would get a permission error rather
    than a filtered list.
    """
    return 1 if company_scoped_rates() else 0


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
def get_boq_line_rates(item_code, rate_source=None, price_list=None, company=None):
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

    analysis = get_rate_analysis_for_item(item_code, company)
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
    filters = {"status": "Approved", "is_active": 1, "item_code": ["is", "set"]}
    if doc.company and company_scoped_rates():
        filters["company"] = doc.company
    for r in frappe.get_all(
        "Rate Analysis",
        filters=filters,
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
                    "amount": flt(r.amount),
                }
                for r in ra.resources
            ],
        },
        default=str,
    )


# How a line's resources were arrived at. Shown wherever they are listed,
# because "what this was priced at" and "what the library says today" are
# different answers and only one of them is defensible in a claim.
AS_PRICED = "As priced"
LIVE = "Live analysis"
NO_ANALYSIS = "No analysis"


def resources_behind_line(rate_build_up=None, rate_analysis=None):
    """What one line of work is made of, and where that came from.

    Prefers the frozen build-up the line was priced with; falls back to the
    library as it stands today, clearly labelled. One reading, used by the
    take-off report and the breakdown on the form alike, so the two can never
    tell a different story about the same line.

    `output_qty` comes back with the resources rather than divided into them:
    an analysis priced for a 10 m³ batch states its resources for the batch,
    and the caller decides whether it wants per unit or per bill.
    """
    if rate_build_up:
        try:
            frozen = json.loads(rate_build_up)
        except (ValueError, TypeError):
            frozen = None
        if frozen and frozen.get("resources"):
            return {
                "source": AS_PRICED,
                "output_qty": flt(frozen.get("output_qty")) or 1,
                "rate_analysis": frozen.get("rate_analysis"),
                "resources": frozen["resources"],
            }

    if rate_analysis and frappe.db.exists("Rate Analysis", rate_analysis):
        ra = frappe.get_cached_doc("Rate Analysis", rate_analysis)
        return {
            "source": LIVE,
            "output_qty": flt(ra.output_qty) or 1,
            "rate_analysis": ra.name,
            "resources": [
                {
                    "type": r.resource_type,
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
                }
                for r in ra.resources
            ],
        }

    return {
        "source": NO_ANALYSIS,
        "output_qty": 1,
        "rate_analysis": rate_analysis or None,
        "resources": [],
    }


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


# ── The whole of a priced document, on one screen ───────────────────────────


BREAKDOWN_DOCTYPES = {
    # doctype: (selling rate field, selling amount field, cost rate field, cost amount field)
    "BOQ": ("rate", "amount", "cost_rate", None),
    "Cost Estimation": (None, None, "unit_cost", "total_cost"),
}


@frappe.whitelist()
def get_work_breakdown(doctype, docname):
    """A bill or an estimate at all three of its levels at once.

    Section, then the work under it, then what that work is made of. The grid
    shows one level and the build-up dialog shows another one line at a time,
    so the document every quantity surveyor actually reads — the one where you
    can see the cement under every trade and what each section comes to — could
    only be produced by exporting it.

    The cost side is what each line is *made* of, priced from the same build-up
    the take-off walks, so a section whose resources do not add up to its cost
    is visible rather than buried.
    """
    if doctype not in BREAKDOWN_DOCTYPES:
        frappe.throw(_("No breakdown is defined for {0}").format(doctype))
    doc = frappe.get_doc(doctype, docname)
    doc.check_permission("read")
    rate_field, amount_field, cost_rate_field, cost_amount_field = BREAKDOWN_DOCTYPES[doctype]

    sections, order = {}, []
    for row in doc.items:
        title = (row.get("boq_section") or "").strip() or _("Unsectioned")
        if title not in sections:
            sections[title] = []
            order.append(title)
        sections[title].append(_breakdown_line(row, rate_field, amount_field,
                                               cost_rate_field, cost_amount_field))

    out_sections, totals = [], {"amount": 0, "cost_amount": 0, "resource_amount": 0}
    for title in order:
        lines = sections[title]
        section = {
            "title": title,
            "lines": lines,
            "amount": sum(flt(l["amount"]) for l in lines),
            "cost_amount": sum(flt(l["cost_amount"]) for l in lines),
            "resource_amount": sum(flt(l["resource_amount"]) for l in lines),
        }
        for key in totals:
            totals[key] += section[key]
        out_sections.append(section)

    return {
        "doctype": doctype,
        "name": doc.name,
        "currency": doc.get("currency"),
        "shows_selling": bool(rate_field),
        "sections": out_sections,
        "totals": totals,
    }


def _breakdown_line(row, rate_field, amount_field, cost_rate_field, cost_amount_field):
    """One line of work, with the resources it is priced from underneath it."""
    qty = flt(row.get("qty"))
    cost_rate = flt(row.get(cost_rate_field))
    cost_amount = flt(row.get(cost_amount_field)) if cost_amount_field else cost_rate * qty
    found = resources_behind_line(row.get("rate_build_up"), row.get("rate_analysis_ref"))
    output_qty = flt(found["output_qty"]) or 1

    resources = []
    for res in found["resources"]:
        # An analysis priced for a batch states its resources for the whole
        # batch; bring them back to one unit before scaling by the line.
        qty_per_unit = flt(res.get("qty")) / output_qty
        total_qty = qty * qty_per_unit
        resources.append({
            "type": res.get("type"),
            "item": res.get("resource_item"),
            "description": res.get("description") or res.get("resource_item"),
            "uom": res.get("uom"),
            "qty_per_unit": qty_per_unit,
            "total_qty": total_qty,
            "rate": flt(res.get("rate")),
            "amount": total_qty * flt(res.get("rate")),
        })

    return {
        "idx": row.idx,
        "item_no": row.get("item_no"),
        "item_code": row.get("item_code"),
        "description": row.get("description"),
        "uom": row.get("uom"),
        "qty": qty,
        "rate": flt(row.get(rate_field)) if rate_field else cost_rate,
        "amount": flt(row.get(amount_field)) if amount_field else cost_amount,
        "cost_rate": cost_rate,
        "cost_amount": cost_amount,
        "analysis": found["rate_analysis"],
        "source": found["source"],
        "resources": resources,
        "resource_amount": sum(flt(r["amount"]) for r in resources),
    }


@frappe.whitelist()
def get_estimate_position(cost_estimation):
    """Where the job stands against the plan this estimate set.

    The estimate is the document everything downstream is measured against —
    the budget is seeded from it, material is bought off its take-off and
    consumption is checked against it — and none of that was visible from the
    estimate itself. Five figures, each from the document that owns it, so the
    answer is never a second copy of anything.
    """
    doc = frappe.get_doc("Cost Estimation", cost_estimation)
    doc.check_permission("read")

    out = {
        "currency": doc.currency,
        "project": doc.project,
        "estimated": flt(doc.total_estimated_cost),
        "budget": None,
        "budget_status": None,
        "actual": None,
        "ordered": 0,
        "planned_value": 0,
        "consumed_value": 0,
        "lines": len(doc.items),
        "unpriced": sum(1 for r in doc.items if not r.rate_analysis_ref),
    }
    if not doc.project:
        return out

    budget = frappe.db.get_value(
        "Project Budget",
        {"project": doc.project, "docstatus": ("<", 2)},
        ["name", "status", "total_budget", "total_actual_cost"],
        as_dict=True,
    )
    if budget:
        out.update({
            "budget_ref": budget.name,
            "budget": flt(budget.total_budget),
            "budget_status": budget.status,
            "actual": flt(budget.total_actual_cost),
        })

    # Committed: what is on order against this job and not closed.
    out["ordered"] = flt(
        frappe.db.sql(
            """
            SELECT SUM(i.base_amount)
            FROM `tabPurchase Order Item` i JOIN `tabPurchase Order` p ON p.name = i.parent
            WHERE i.project = %s AND p.docstatus = 1 AND p.status NOT IN ('Closed', 'Cancelled')
            """,
            doc.project,
        )[0][0]
    )

    position = get_consumption_position(doc.project)
    out["planned_value"] = flt(position.get("allowed_value"))
    out["consumed_value"] = flt(position.get("used_value"))
    return out


# ── Helping the buyer get it right, rather than telling them afterwards ─────


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def work_items_for_project(doctype, txt, searchfield, start, page_len, filters):
    """The lines of work a project is actually doing.

    `cms_work_item` offered every service item on the site, so a purchase for
    one job could be attributed to another job's work — and nothing said so
    until the server refused the save, by which time the whole document had
    been typed. The picker now offers the project's own estimate.

    A project with no estimate falls back to every service item: an empty
    picker on a job nobody has costed yet is a dead end, and the missing
    estimate is its own complaint.
    """
    from construction_management_suite.utils.validations import current_estimate

    project = (filters or {}).get("project")
    estimate = current_estimate(project) if project else None
    like = "%%%s%%" % (txt or "")

    if not estimate:
        # No plan to scope to, so the only rule left is the one that always
        # holds: a line of work is a service item. This used to live on the
        # custom field as a `link_filters`, which quietly disabled this query
        # altogether — see setup._work_ref_fields.
        return frappe.db.sql(
            """SELECT name, item_name FROM `tabItem`
               WHERE is_stock_item = 0 AND disabled = 0
                 AND (name LIKE %(txt)s OR item_name LIKE %(txt)s)
               ORDER BY name LIMIT %(start)s, %(page_len)s""",
            {"txt": like, "start": start, "page_len": page_len},
        )

    return frappe.db.sql(
        """SELECT i.item_code, i.description
           FROM `tabCost Estimation Item` i
           WHERE i.parent = %(estimate)s
             AND (i.item_code LIKE %(txt)s OR i.description LIKE %(txt)s)
           ORDER BY i.idx LIMIT %(start)s, %(page_len)s""",
        {"estimate": estimate, "txt": like, "start": start, "page_len": page_len},
    )


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def materials_for_work(doctype, txt, searchfield, start, page_len, filters):
    """The materials the plan holds for one line of work.

    Once a row says which work it is for, the question "which item?" has one
    short answer — the resources behind that work's rate analysis — and the
    picker should give it. Offering every item on the site instead is how the
    wrong cement grade, or a material belonging to another trade, gets bought
    against work that never asked for it; and the person typing has no way to
    know, because the plan lives two screens away.

    Falls back to every stock item when the work has nothing behind it, the
    same way the work picker falls back when a project has no estimate: an
    empty picker is a dead end, and a job does buy things nobody foresaw. The
    plan check still says so on save.
    """
    from construction_management_suite.material_planning.doctype.material_consumption_entry.material_consumption_entry import (
        take_off_by_line,
    )
    from construction_management_suite.utils.validations import current_estimate

    f = filters or {}
    project, work, company = f.get("project"), f.get("work_item"), f.get("company")

    planned = []
    if project and work and current_estimate(project):
        planned = sorted({code for (code, w) in take_off_by_line(project) if w == work and code})

    where = ["i.is_stock_item = 1", "i.disabled = 0", "(i.name LIKE %(txt)s OR i.item_name LIKE %(txt)s)"]
    values = {"txt": "%%%s%%" % (txt or ""), "start": start, "page_len": page_len}
    if planned:
        where.append("i.name IN %(planned)s")
        values["planned"] = planned
    # An item master that has been given a company belongs to one — the same
    # scope every other picker in this module applies.
    if company and frappe.get_meta("Item").has_field("company"):
        where.append("IFNULL(i.company, '') IN ('', %(company)s)")
        values["company"] = company

    return frappe.db.sql(
        """SELECT i.name, i.item_name FROM `tabItem` i
           WHERE {where} ORDER BY i.name LIMIT %(start)s, %(page_len)s""".format(
            where=" AND ".join(where)
        ),
        values,
    )


@frappe.whitelist()
def line_against_plan(project, item_code, work_item=None, qty=0):
    """What the plan says about this material for this work, as the row is typed.

    Said at the moment of choosing rather than at the moment of saving, and
    only when there is something to say. A line that matches the plan is not
    worth a message — the buyer is doing their job, and an app that comments on
    correct work teaches people to dismiss it without reading. It speaks when
    the material is not in the plan, when it is planned for different work, or
    when the quantity goes past what is left to buy.
    """
    if not project or not item_code:
        return {}
    from construction_management_suite.material_planning.doctype.material_consumption_entry.material_consumption_entry import (
        take_off_by_line,
    )
    from construction_management_suite.utils.validations import (
        current_estimate,
        runs_without_a_cost_plan,
    )

    if not current_estimate(project):
        # A project run deliberately without one is not missing anything, and
        # saying so on every line would be the noise this whole panel avoids.
        if runs_without_a_cost_plan(project):
            return {}
        return {"state": "no-plan", "message": _("{0} has no Cost Estimation to buy against.").format(project)}

    plan = take_off_by_line(project)
    key = (item_code, work_item or None)
    entry = plan.get(key)
    if not entry:
        elsewhere = sorted({w for (code, w) in plan if code == item_code and w})
        if not elsewhere:
            return {
                "state": "unplanned",
                "message": _("{0} is not in {1}'s plan{2}.").format(
                    item_code, project, _(" for {0}").format(work_item) if work_item else ""
                ),
            }
        if not work_item:
            # The plan is keyed by material AND work, so a line that has not
            # said which work it is for matches nothing — which is not the same
            # as being unplanned. Nothing is wrong here and nothing is said:
            # the material is planned, and the work can be named next.
            return {"state": "needs-work"}
        return {
            "state": "wrong-work",
            "message": _("{0} is not planned for {1} — the plan has it under {2}.").format(
                item_code, work_item, ", ".join(elsewhere[:3])
            ),
        }

    outstanding = {
        (r["item_code"], r["cms_work_item"]): r for r in take_off_outstanding(project)
    }
    left = flt((outstanding.get(key) or {}).get("qty"))
    answer = {
        "state": "planned",
        "planned": flt(entry["qty"]),
        "uom": entry["uom"],
        "left": left,
    }
    if flt(qty) > left + 0.0001:
        answer["state"] = "over"
        answer["message"] = _(
            "{0} {1} on this line, but the plan has {2} for {3} and only {4} is "
            "still to buy."
        ).format(
            frappe.utils.fmt_money(flt(qty), precision=2), entry["uom"] or "",
            frappe.utils.fmt_money(flt(entry["qty"]), precision=2),
            work_item or project,
            frappe.utils.fmt_money(left, precision=2),
        )
    return answer


@frappe.whitelist()
def get_buying_context(project):
    """What the buyer needs on screen while writing a purchase: the money left,
    and where each material stands against the plan.

    Both figures existed already — one on the Project Budget, one in the
    Material Position report — and neither was anywhere near the document being
    written. A buyer had to leave the order, read two screens and come back,
    which nobody does; so orders were written against a plan nobody was looking
    at, and the first anyone heard of it was a refusal on save.
    """
    if not project:
        return {}
    frappe.has_permission("Project", "read", doc=project, throw=True)
    from construction_management_suite.material_planning.report.material_position.material_position import (
        execute as material_position,
    )

    company = frappe.db.get_value("Project", project, "company")
    currency = frappe.get_cached_value("Company", company, "default_currency")

    budget = frappe.db.get_value(
        "Project Budget",
        {"project": project, "docstatus": ("<", 2)},
        ["name", "status", "total_budget", "total_actual_cost"],
        as_dict=True,
    ) or {}
    # Read live rather than from the budget's stored figure, which only moves
    # when somebody saves the budget.
    committed = flt(
        frappe.db.sql(
            """SELECT SUM(grand_total - advance_paid) FROM `tabPurchase Order`
               WHERE project = %s AND docstatus = 1 AND status NOT IN ('Completed', 'Closed', 'Cancelled')""",
            project,
        )[0][0]
    )

    lines = []
    for row in material_position({"project": project, "by_work": 1})[1]:
        if not row.get("item_code"):
            continue
        lines.append({
            "item_code": row.get("item_code"),
            "work": row.get("work"),
            "uom": row.get("uom"),
            "required": flt(row.get("required")),
            "ordered": flt(row.get("ordered")),
            "received": flt(row.get("received")),
            "consumed": flt(row.get("consumed")),
            "balance": flt(row.get("balance")),
        })

    return {
        "project": project,
        "currency": currency,
        "budget": {
            "name": budget.get("name"),
            "status": budget.get("status"),
            "total": flt(budget.get("total_budget")),
            "actual": flt(budget.get("total_actual_cost")),
            "committed": committed,
            "left": flt(budget.get("total_budget")) - flt(budget.get("total_actual_cost")) - committed,
        },
        "lines": lines,
    }
