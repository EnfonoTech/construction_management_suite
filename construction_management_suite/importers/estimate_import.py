"""Import a cost estimate from one spreadsheet: items, analyses, estimate.

`misk_mawallah.py` reads one client's workbook and knows its shape. This reads
a template anybody can fill in, which is the same shape generalised: **one row
per resource**, each row naming the line of work it belongs to.

    Section | Work | Work Code | Work UOM | Work Qty | Category
    Resource Type | Item Code | Description | UOM | Qty | Rate

Rows sharing a Work become one Rate Analysis under one work Item, and every
work becomes a line on one Cost Estimation.

Everything it creates is a **draft**: the analyses are Draft, the estimate is
Draft, and nothing is submitted. An import is a proposal — somebody reads it,
fixes what the spreadsheet got wrong, and submits it. Nothing downstream reads
a draft, which is the point.

Two conventions come straight from the other importer, for the same reasons:

* **`output_qty` is the work quantity, and the estimate line carries the same
  figure.** `_take_off_rows` computes `resource.qty / output_qty * line_qty`,
  so the two being equal makes the take-off reproduce the sheet's own
  quantities. Leave Work Qty blank and both become 1, which is the `1 Ls` case.
* **Each material Item is held in the unit its rows are priced in.**
  `validate_uom_convertible` refuses a resource whose unit cannot be converted
  to its Item's stock unit, and the trade UOMs have no conversion factors.
"""

import csv
import io
import os
import re

import frappe
from frappe import _
from frappe.utils import cint, flt

from construction_management_suite.utils.settings import service_materials_allowed

# The template's columns. Lower-cased and stripped before matching, so a filled
# -in sheet may differ in case and spacing.
COLUMNS = (
    ("section", False),
    ("work", True),
    ("work code", False),
    ("work uom", False),
    ("work qty", False),
    ("category", False),
    ("resource type", True),
    ("item code", False),
    ("description", True),
    ("uom", True),
    ("qty", True),
    ("rate", True),
)

RESOURCE_TYPES = ("Material", "Labour", "Equipment", "Subcontract", "Overhead")

CATEGORIES = ("Civil", "Mechanical", "Electrical", "Plumbing", "Finishes",
              "External Works", "Provisional")

TEMPLATE_ROWS = [
    ["A - Substructure", "Structural Works", "", "Ls", "1", "Civil",
     "Material", "", "Cement OPC 50kg", "Bag", "50", "1.53"],
    ["A - Substructure", "Structural Works", "", "Ls", "1", "Civil",
     "Subcontract", "", "Excavation contract", "Ls", "1", "1400"],
    ["A - Substructure", "Structural Works", "", "Ls", "1", "Civil",
     "Labour", "", "Steel fixing gang", "Day", "12", "35"],
    ["B - Superstructure", "Blockwork", "", "Square Meter", "250", "Civil",
     "Material", "", "200mm hollow block", "Nos", "3125", "0.42"],
]


# ── the sheet ──────────────────────────────────────────────────────────────


def template_csv():
    """The template, as text. One row per resource; repeat Work down the rows."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([name.title() for name, _r in COLUMNS])
    writer.writerows(TEMPLATE_ROWS)
    return out.getvalue()


def template_bytes():
    """The same, with a byte order mark, because Excel guesses otherwise.

    Excel opens a .csv in the machine's own codepage unless the file starts
    with a BOM, so a UTF-8 em dash arrives as `â€"` and an Arabic or accented
    description arrives as mojibake. Three bytes at the front settle it.

    The reader already strips a BOM — it opens with `utf-8-sig` — so a file
    saved back out of Excel comes home cleanly.
    """
    return ("\ufeff" + template_csv()).encode("utf-8")


def _norm(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _read_rows(path):
    """Every data row of a csv or xlsx, as dicts keyed by the header."""
    if path.lower().endswith((".xlsx", ".xlsm")):
        import openpyxl

        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
        grid = [list(r) for r in book[book.sheetnames[0]].iter_rows(values_only=True)]
    else:
        with io.open(path, encoding="utf-8-sig") as handle:
            grid = [row for row in csv.reader(handle)]

    grid = [row for row in grid if any(_norm(cell) for cell in row)]
    if not grid:
        frappe.throw(_("That file has no rows in it."))

    header = [_norm(cell).lower() for cell in grid[0]]
    missing = [name for name, required in COLUMNS if required and name not in header]
    if missing:
        frappe.throw(
            _("These columns are missing from the file: {0}<br><br>Download the "
              "template from Construction Settings and fill that in — the column "
              "names are how the file is read.").format(", ".join(missing))
        )
    return [
        {header[i]: cell for i, cell in enumerate(row) if i < len(header)}
        for row in grid[1:]
    ]


def _number(value):
    """A spreadsheet writes 1,287.50 and ' 12 ' and sometimes nothing."""
    if value in (None, ""):
        return 0.0
    return flt(str(value).replace(",", "").strip())


def parse(path):
    """The file, grouped into works with their resources, or a clear complaint."""
    works, problems = {}, []
    for n, raw in enumerate(_read_rows(path), start=2):
        work = _norm(raw.get("work"))
        description = _norm(raw.get("description"))
        kind = _norm(raw.get("resource type")).title()
        uom = _norm(raw.get("uom"))

        if not work:
            problems.append(_("Row {0}: no Work").format(n))
            continue
        if not description:
            problems.append(_("Row {0}: no Description").format(n))
            continue
        if kind not in RESOURCE_TYPES:
            problems.append(
                _("Row {0}: Resource Type {1} is not one of {2}").format(
                    n, kind or "(blank)", ", ".join(RESOURCE_TYPES))
            )
            continue
        if not uom or not frappe.db.exists("UOM", uom):
            problems.append(_("Row {0}: UOM {1} is not on this site").format(n, uom or "(blank)"))
            continue

        entry = works.setdefault(work, {
            "work": work,
            "code": _norm(raw.get("work code")) or work,
            "uom": _norm(raw.get("work uom")) or "Ls",
            "qty": _number(raw.get("work qty")) or 1,
            "category": _norm(raw.get("category")) or None,
            "section": _norm(raw.get("section")) or None,
            "rows": [],
        })
        entry["rows"].append({
            "line": n,
            "type": kind,
            "code": _norm(raw.get("item code")) or description,
            "description": description,
            "uom": uom,
            "qty": _number(raw.get("qty")),
            "rate": _number(raw.get("rate")),
        })

    for entry in works.values():
        if entry["category"] and entry["category"] not in CATEGORIES:
            problems.append(
                _("{0}: Category {1} is not one of {2}").format(
                    entry["work"], entry["category"], ", ".join(CATEGORIES))
            )
        if not frappe.db.exists("UOM", entry["uom"]):
            problems.append(_("{0}: Work UOM {1} is not on this site").format(
                entry["work"], entry["uom"]))
    return works, problems


# ── what it builds ─────────────────────────────────────────────────────────


def _item_group(kind):
    """A group per kind, created once, so everything can be filtered by what it is."""
    from construction_management_suite.importers.misk_mawallah import (
        GROUP_CANDIDATES,
        _item_group as resolve,
    )

    key = "material" if kind == "Material" else kind
    return resolve(key if key in GROUP_CANDIDATES else "material")


def _ensure_item(code, name, kind, uom, company):
    """One Item, held in the unit its rows are priced in.

    Never touched if it already exists. An import brings a plan in; it does not
    get to restate what an item already on the site is.
    """
    # `frappe.db.exists` compares case-insensitively, so asking for
    # "Excavation Contract" finds "Excavation contract" and reports it there.
    # Return the name it is ACTUALLY stored under: everything downstream — the
    # take-off key, the plan check, a dict lookup — compares exactly, and a
    # reference in the other case matches nothing.
    existing = frappe.db.get_value("Item", code, "name")
    if existing:
        return existing
    service = kind != "Material" or service_materials_allowed()
    doc = frappe.get_doc({
        "doctype": "Item",
        "item_code": code,
        "item_name": name[:140],
        "description": name,
        "item_group": _item_group(kind),
        "stock_uom": uom,
        "is_stock_item": 0 if service else 1,
        "is_purchase_item": 1,
        "is_sales_item": 1 if kind == "work" else 0,
    })
    if company and doc.meta.has_field("company"):
        doc.company = company
    doc.insert(ignore_permissions=True)
    return code


@frappe.whitelist()
def run(file_url=None, project=None, company=None, dry=1):
    """Read the file and build the estimate. Writes nothing unless `dry` is 0.

    Returns a dict the form renders, rather than printing: this is called from
    a button, not a terminal.
    """
    dry = cint(dry)
    if not file_url:
        frappe.throw(_("Attach the filled-in template first."))
    if not project:
        frappe.throw(_("Name the project this estimate is for."))
    if not frappe.db.exists("Project", project):
        frappe.throw(_("No project called {0}").format(project))

    company = company or frappe.db.get_value("Project", project, "company")
    if not company:
        frappe.throw(_("{0} has no company on it").format(project))
    currency = frappe.get_cached_value("Company", company, "default_currency")

    path = frappe.get_doc("File", {"file_url": file_url}).get_full_path()
    if not os.path.exists(path):
        frappe.throw(_("That attachment is no longer on disk."))

    works, problems = parse(path)
    if problems:
        frappe.throw(
            "<br>".join(problems[:15])
            + (_("<br>… and {0} more").format(len(problems) - 15) if len(problems) > 15 else ""),
            title=_("{0} problem(s) in the file").format(len(problems)),
        )
    if not works:
        frappe.throw(_("Nothing to import — the file has a header and no rows."))

    # An estimate is one per project, enforced on submit. Say so before writing
    # 300 Items rather than after.
    existing = frappe.db.get_value(
        "Cost Estimation", {"project": project, "docstatus": ("<", 2)}, "name")
    if existing and not dry:
        frappe.throw(
            _("{0} already has a Cost Estimation: {1}. A project has one — cancel "
              "and amend it, or import into another project.").format(project, existing)
        )

    summary = {
        "project": project, "dry": dry, "works": len(works),
        "rows": sum(len(w["rows"]) for w in works.values()),
        "value": sum(r["qty"] * r["rate"] for w in works.values() for r in w["rows"]),
        "by_kind": {}, "new_items": 0, "estimate": None, "existing": existing,
        "lines": [],
    }
    for work in works.values():
        for row in work["rows"]:
            summary["by_kind"][row["type"]] = summary["by_kind"].get(row["type"], 0) + 1
        summary["lines"].append({
            "work": work["work"], "code": work["code"], "uom": work["uom"],
            "qty": work["qty"], "category": work["category"],
            "section": work["section"],
            "resources": len(work["rows"]),
            "cost": sum(r["qty"] * r["rate"] for r in work["rows"]),
        })

    wanted = {w["code"] for w in works.values()} | {
        r["code"] for w in works.values() for r in w["rows"]}
    summary["new_items"] = sum(1 for code in wanted if not frappe.db.exists("Item", code))

    if dry:
        return summary

    lines = []
    for work in works.values():
        _ensure_item(work["code"], work["work"], "work", work["uom"], company)
        resources = []
        for row in work["rows"]:
            _ensure_item(row["code"], row["description"], row["type"], row["uom"], company)
            resources.append({
                "resource_type": row["type"],
                "resource_item": row["code"],
                "description": row["description"],
                "uom": row["uom"],
                "qty": row["qty"],
                "rate": row["rate"],
                "amount": row["qty"] * row["rate"],
            })
        analysis = frappe.get_doc({
            "doctype": "Rate Analysis",
            "analysis_name": work["work"][:140],
            "item_code": work["code"],
            "uom": work["uom"],
            "company": company,
            "currency": currency,
            # The take-off divides by this and multiplies by the estimate line's
            # quantity, so the two must agree or every material figure is wrong
            # while the money still totals correctly.
            "output_qty": work["qty"],
            "status": "Draft",
            "is_active": 1,
            "rate_basis": "Manual",
            "resources": resources,
        })
        analysis.insert(ignore_permissions=True)
        lines.append({
            "item_code": work["code"],
            "description": work["work"],
            "boq_section": work["section"],
            "work_category": work["category"],
            "uom": work["uom"],
            "qty": work["qty"],
            "rate_analysis_ref": analysis.name,
        })

    estimate = frappe.get_doc({
        "doctype": "Cost Estimation",
        "project": project, "company": company, "currency": currency,
        "estimation_date": frappe.utils.nowdate(),
        "status": "Draft",
        # The sheet's total is the total; a contingency is a decision somebody
        # takes on top of a cost plan, not something an import invents.
        "contingency_percent": 0,
        "items": lines,
    })
    estimate.insert(ignore_permissions=True)
    summary["estimate"] = estimate.name
    summary["total"] = flt(estimate.total_estimated_cost)
    return summary


@frappe.whitelist()
def download_template():
    """Hand the browser the template as a csv download."""
    frappe.response["filename"] = "cost_estimate_template.csv"
    frappe.response["filecontent"] = template_bytes()
    frappe.response["type"] = "binary"
