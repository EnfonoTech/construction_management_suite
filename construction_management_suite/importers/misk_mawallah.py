"""Import the Misk Mawallah cost sheet as a Cost Estimation.

The workbook is three documents in one file. This reads exactly one of them:
the `Misk Format` sheet, which is the contractor's own cost estimate — 21
lettered trade sections, 389 priced rows, and four tracking columns
(LPO/CONTRACT, ACTUAL, BALANCE TO PAY, REMARKS) that are entirely empty. That
emptiness is the reason for the import: somebody laid out a procurement tracker
in a spreadsheet and never filled one cell of it.

The client-facing bills and the QS measurement sheets are deliberately not
touched. They are different documents for different audiences and they are cut
differently; mixing them into one pass is how an import discovers halfway
through that its two halves cannot be reconciled.

What comes out
--------------
One work Item per section, one material Item per distinct material description,
one Rate Analysis per section and one Cost Estimation line per section.

Two conventions hold this together and neither is negotiable:

**`output_qty = 1`, estimate line qty 1.** `_take_off_rows` computes
`resource.qty / output_qty * line_qty`, so this makes the take-off reproduce
the sheet's own quantities exactly. Any other output quantity requires dividing
all 389 sheet figures first. Get that backwards and the money still totals
correctly — `rate_per_unit * line_qty` is unaffected — while every material
quantity is wrong and nothing on screen says so.

**Each material Item is held in the unit the sheet prices it in.** Not a
generic one. `validate_uom_convertible` fires when a Rate Analysis is approved
and refuses a resource whose unit cannot be converted to its Item's stock unit,
and none of the trade UOMs this app creates — Bag, Roll, Ls, Trip, Drum, RM —
has a conversion factor to anything. Holding the Item in its own sheet unit
makes the two equal and the question disappear. It is also simply true: an item
bought by the bag is held by the bag.

Running it
----------
    bench --site misk execute construction_management_suite.importers.misk_mawallah.run
    bench --site misk execute construction_management_suite.importers.misk_mawallah.run \
        --kwargs "{'sections': 'A,E', 'project': 'MM-TRIAL', 'dry': 0}"

`dry=1` is the default and writes nothing.
"""

import csv
import os
import re

import frappe
from frappe.utils import flt

WORKBOOK = "Misk Mawallah.xlsx"
CLASSIFICATION = "Misk Mawallah - classification.csv"
SHEET = "Misk Format"

# Columns on `Misk Format`, zero-based.
C_NO, C_DESC, C_UNIT, C_QTY, C_RATE, C_AMOUNT = 0, 1, 2, 3, 4, 5

# What the sheet writes, and the UOM it means. Spelling and case only — no unit
# is converted into another, because an Item held in the sheet's own unit needs
# no conversion factor and cannot disagree with its own resource rows.
UOM = {
    "sqm": "Square Meter", "m2": "Square Meter",
    "m3": "Cubic Meter",
    "m": "Meter", "rm": "RM",
    "kg": "Kg", "ton": "Tonne",
    "nos": "Nos", "items": "Nos",
    "ls": "Ls", "bag": "Bag", "roll": "Roll", "trip": "Trip", "drum": "Drum",
    "qtn": "Qtn", "ctn": "Ctn", "pkt": "Pkt", "box": "Box",
    "days": "Day", "month": "Month",
}

# A work Item per section; a material Item per description.
WORK_CODE = "MM-{section}"
MATERIAL_CODE = "MM-M-{n:03d}"

# The trade each section belongs to, which is what the Resource Take-off groups
# by. The sheet's own lettering already runs in trade order — civil first, then
# electrical, then plumbing — so the map is ranges, not a line per section.
#
# U is the one judgement call: Main Connections is the water supply, the water
# meter and the Haya connection, which are outside the building, plus the fire
# fighting subcontract, which is not. External Works is the closest of the
# seven; change this line if the QS disagrees.
SECTION_CATEGORY = dict(
    [(c, "Civil") for c in "ABCDEFGHIJK"]
    + [(c, "Electrical") for c in "LMNOP"]
    + [(c, "Plumbing") for c in "QRS"]
    + [("T", "Mechanical"), ("U", "External Works")]
)


# ── Reading ────────────────────────────────────────────────────────────────


def _app_dir():
    """The repo root, where the workbook sits — one level above the module."""
    return os.path.dirname(frappe.get_app_path("construction_management_suite"))


def _uom(raw):
    key = re.sub(r"\s+", "", str(raw or "")).lower()
    return UOM.get(key)


def read_sheet(path=None):
    """The cost sheet, as sections each holding their own priced rows.

    A section opens on a row whose first cell is a single letter; a priced row
    carries a number there. Anything else — blank rows, the area note at the
    foot — is not data and is skipped rather than guessed at.
    """
    import openpyxl

    path = path or os.path.join(_app_dir(), WORKBOOK)
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[SHEET]

    sections, current = {}, None
    for r, row in enumerate(ws.iter_rows(values_only=True), 1):
        cell = lambda i: row[i] if len(row) > i else None
        ref, desc = cell(C_NO), cell(C_DESC)

        if isinstance(ref, str) and len(ref.strip()) == 1 and ref.strip().isalpha() and desc:
            current = ref.strip().upper()
            sections[current] = {"section": current, "title": str(desc).strip(), "rows": []}
            continue

        if current and isinstance(ref, (int, float)) and desc:
            sections[current]["rows"].append({
                "row": r,
                "no": ref,
                "description": re.sub(r"\s+", " ", str(desc)).strip(),
                "unit_raw": str(cell(C_UNIT) or "").strip(),
                "uom": _uom(cell(C_UNIT)),
                "qty": flt(cell(C_QTY)),
                "rate": flt(cell(C_RATE)),
                "amount": flt(cell(C_AMOUNT)),
            })
    return sections


def read_classification(path=None):
    """Resource type per sheet row, keyed by the row number it came from.

    The classification was done by hand against this workbook and its `Row`
    column holds the sheet's own row numbers — all 396 of them resolve — so the
    two are joined on that rather than on description text, which has been
    edited on both sides.
    """
    path = path or os.path.join(_app_dir(), CLASSIFICATION)
    out = {}
    with open(path, encoding="utf-8-sig") as fh:
        for line in csv.DictReader(fh):
            out[int(line["Row"])] = {
                "type": (line.get("Resource Type (edit me)") or "").strip(),
                "note": (line.get("Note") or "").strip(),
            }
    return out


def load(sections=None):
    """The sheet and the classification, joined, ready to build from."""
    sheet = read_sheet()
    kinds = read_classification()
    wanted = [s.strip().upper() for s in sections.split(",")] if sections else list(sheet)

    picked = {}
    for code in wanted:
        if code not in sheet:
            frappe.throw(f"Section {code} is not on the sheet. Sections: {', '.join(sorted(sheet))}")
        sec = dict(sheet[code])
        sec["rows"] = [dict(r, **kinds.get(r["row"], {"type": "", "note": ""})) for r in sec["rows"]]
        picked[code] = sec
    return picked


# ── Building ───────────────────────────────────────────────────────────────


def _material_codes(sections):
    """One code per distinct material description, across every section read.

    14 descriptions appear in more than one section — wash sand under
    Structural, Block Works and Tiles; cement under half the trades. They must
    resolve to **one** Item, because that is what lets the take-off key on
    `(material, work item)` and answer "how much sand does this job need, and
    for which trade". Numbered in first-seen order and keyed on the normalised
    description, never on the text itself, which will be edited.
    """
    codes, n = {}, 0
    for sec in sections.values():
        for row in sec["rows"]:
            if row["type"] != "Material":
                continue
            key = row["description"].strip().lower()
            if key not in codes:
                n += 1
                codes[key] = MATERIAL_CODE.format(n=n)
    return codes


def _item_group(kind):
    """An Item Group that exists on any ERPNext site."""
    return "Services" if kind == "work" else "Raw Material"


def _ensure_item(code, name, kind, uom, company=None, dry=True, log=None):
    """One Item, held in the unit the sheet prices it in.

    The stock unit is the whole point. `validate_uom_convertible` refuses a
    resource whose unit cannot be converted to its Item's stock unit, and none
    of the trade UOMs has a conversion factor — so an Item held in anything but
    its own sheet unit makes the analysis unapprovable.
    """
    exists = frappe.db.exists("Item", code)
    if log is not None:
        log.append(("item", code, name[:46], uom, "exists" if exists else "new"))
    if dry or exists:
        return code

    doc = frappe.get_doc({
        "doctype": "Item",
        "item_code": code,
        "item_name": name[:140],
        "description": name,
        "item_group": _item_group(kind),
        "stock_uom": uom,
        "is_stock_item": 0 if kind == "work" else 1,
        "is_purchase_item": 1,
        "is_sales_item": 1 if kind == "work" else 0,
        "include_item_in_manufacturing": 0,
    })
    if company and doc.meta.has_field("company"):
        doc.company = company
    doc.insert(ignore_permissions=True)
    return code


def _analysis_for(sec, codes, company, currency, dry=True, log=None):
    """One Rate Analysis per section, output quantity 1.

    The section has no single measured quantity to divide by — Tiles covers six
    tile types in three units — so one lot of the section is the unit, the
    sheet's own figures go in undivided, and the take-off reproduces them.
    """
    work = WORK_CODE.format(section=sec["section"])
    resources, skipped = [], []
    for row in sec["rows"]:
        kind = row["type"] or "Material"
        line = {
            "resource_type": kind,
            "description": row["description"],
            "uom": row["uom"],
            "qty": row["qty"],
            "rate": row["rate"],
            "amount": row["amount"],
        }
        if kind == "Material":
            line["resource_item"] = codes[row["description"].strip().lower()]
        if not row["qty"] and not row["rate"]:
            # A unit but no price: a material the QS knew was needed and had
            # not costed. Kept at zero so the gap is visible and the trade it
            # belongs to is not lost; it contributes nothing to any figure.
            skipped.append(row)
        resources.append(line)

    if log is not None:
        log.append(("analysis", work, sec["title"][:40], f"{len(resources)} resources",
                    f"{len(skipped)} unpriced"))
    if dry:
        return None, resources, skipped

    doc = frappe.get_doc({
        "doctype": "Rate Analysis",
        "analysis_name": f"{sec['section']} — {sec['title']}",
        "item_code": work,
        "uom": "Ls",
        "company": company,
        "currency": currency,
        "output_qty": 1,
        "status": "Draft",
        "is_active": 1,
        "rate_basis": "Manual",
        "resources": resources,
    })
    doc.insert(ignore_permissions=True)
    return doc.name, resources, skipped


def _project(name, company, dry=True):
    """The job, found by its title or created under it.

    `Project` is named by series, so what a person calls the job — MM-TRIAL —
    is its `project_name` and the record is PROJ-nnnn. Looking it up by `name`
    finds nothing and makes a second project on every run.
    """
    found = frappe.db.get_value("Project", {"project_name": name}, "name") \
        or (name if frappe.db.exists("Project", name) else None)
    if found:
        on = frappe.db.get_value("Project", found, "company")
        if on and on != company:
            frappe.throw(f"Project {found} belongs to {on}, not {company}")
        return found
    if dry:
        return name
    doc = frappe.get_doc({
        "doctype": "Project", "project_name": name, "company": company,
        "status": "Open", "is_active": "Yes",
    })
    doc.insert(ignore_permissions=True)
    return doc.name


def run(sections=None, project=None, company=None, dry=1, submit=0):
    """Read the cost sheet and build the estimate. Prints what it did either way.

    `dry=1` — the default — reads everything, resolves every Item and every
    resource, reconciles the money against the sheet and writes nothing. No
    import should go in without that having been read first.
    """
    dry, submit = int(dry), int(submit)
    company = company or frappe.defaults.get_user_default("Company") \
        or frappe.db.get_value("Company", {}, "name")
    currency = frappe.get_cached_value("Company", company, "default_currency")
    project = project or "MM-TRIAL"

    picked = load(sections)
    codes = _material_codes(picked)
    log = []

    # Project is named by series, so the record is PROJ-nnnn and the name asked
    # for is its title. Resolve it before printing anything, or the run reports
    # a project nobody can open.
    wanted = project
    existing = frappe.db.get_value("Project", {"project_name": wanted}, "name")
    print(f"\n{'DRY RUN — nothing is written' if dry else 'IMPORTING'}   "
          f"company={company}  project={existing or wanted}"
          f"{'' if existing else ' (new)'}")
    print(f"sections: {', '.join(sorted(picked))}")

    # ── what the sheet says, before anything is built ──────────────────────
    print(f"\n{'sec':4} {'title':28} {'category':14} {'rows':>5} {'mat':>4} {'lab':>4} "
          f"{'oth':>4} {'sheet total':>14}")
    sheet_total = 0
    for code in sorted(picked):
        sec = picked[code]
        rows = sec["rows"]
        amt = sum(flt(r["amount"]) for r in rows)
        sheet_total += amt
        mat = sum(1 for r in rows if r["type"] == "Material")
        lab = sum(1 for r in rows if r["type"] == "Labour")
        print(f"{code:4} {sec['title'][:27]:28} {SECTION_CATEGORY.get(code, '?'):14} "
              f"{len(rows):>5} {mat:>4} {lab:>4} {len(rows) - mat - lab:>4} {amt:>14,.2f}")
    print(f"{'':4} {'':28} {'':14} {'':>5} {'':>4} {'':>4} {'':>4} {sheet_total:>14,.2f}")

    # ── units, before an Item is written ───────────────────────────────────
    unmapped = sorted({r["unit_raw"] for s in picked.values() for r in s["rows"] if not r["uom"]})
    if unmapped:
        frappe.throw(f"No UOM mapping for: {unmapped}. Add them to importers.misk_mawallah.UOM.")
    print(f"\nunits resolved: "
          f"{sorted({r['uom'] for s in picked.values() for r in s['rows']})}")

    # ── one stock unit per material, or the analysis cannot be approved ────
    units = {}
    for sec in picked.values():
        for row in sec["rows"]:
            if row["type"] != "Material":
                continue
            units.setdefault(codes[row["description"].strip().lower()], set()).add(row["uom"])
    clash = {c: u for c, u in units.items() if len(u) > 1}
    if clash:
        frappe.throw(f"These materials are priced in two units and need splitting: {clash}")

    project = _project(project, company, dry)

    # ── items ──────────────────────────────────────────────────────────────
    for code in sorted(picked):
        sec = picked[code]
        _ensure_item(WORK_CODE.format(section=code), f"{code} — {sec['title']}",
                     "work", "Ls", company, dry, log)
    seen = set()
    for sec in picked.values():
        for row in sec["rows"]:
            if row["type"] != "Material":
                continue
            item = codes[row["description"].strip().lower()]
            if item in seen:
                continue
            seen.add(item)
            _ensure_item(item, row["description"], "material", row["uom"], company, dry, log)

    # ── analyses, then the estimate ────────────────────────────────────────
    lines, unpriced = [], []
    for code in sorted(picked):
        sec = picked[code]
        name, resources, skipped = _analysis_for(sec, codes, company, currency, dry, log)
        unpriced += [(code, r) for r in skipped]
        lines.append({
            "boq_section": f"{code} — {sec['title']}",
            "work_category": SECTION_CATEGORY.get(code),
            "item_code": WORK_CODE.format(section=code),
            "description": sec["title"],
            "uom": "Ls",
            "qty": 1,
            "rate_analysis_ref": name,
        })

    estimate = None
    if not dry:
        doc = frappe.get_doc({
            "doctype": "Cost Estimation",
            "project": project, "company": company, "currency": currency,
            "estimation_date": frappe.utils.nowdate(),
            "status": "Draft",
            # The sheet's total is the total. A contingency is a decision
            # somebody takes on top of a cost plan, not something an import
            # invents — and a default one silently puts the estimate 5% above
            # the document it was built from.
            "contingency_percent": 0,
            "items": lines,
        })
        doc.insert(ignore_permissions=True)
        estimate = doc.name
        if submit:
            doc.submit()

    # ── what happened ──────────────────────────────────────────────────────
    made = [l for l in log if l[0] == "item" and l[4] == "new"]
    print(f"\nwork items      : {sum(1 for l in made if l[1].count('-') == 1)}"
          f"   (of {len(picked)} sections)")
    print(f"material items  : {len(seen)} distinct, from "
          f"{sum(1 for s in picked.values() for r in s['rows'] if r['type'] == 'Material')} rows")
    shared = sum(1 for c, u in units.items()
                 if sum(1 for s in picked.values() for r in s["rows"]
                        if r["type"] == "Material"
                        and codes[r["description"].strip().lower()] == c) > 1)
    print(f"  shared across sections: {shared}")
    print(f"unpriced rows   : {len(unpriced)}"
          + (f"  {[r['description'][:26] for _, r in unpriced[:5]]}" if unpriced else ""))
    print(f"estimate        : {estimate or '(not written — dry run)'}"
          + ("  [DRAFT]" if estimate and not submit else ""))
    if estimate and not submit:
        print("  It is a draft on purpose — read it, then submit it by hand.")
        print("  Nothing downstream reads a draft: no take-off, no Material")
        print("  Position, no plan checks on purchases until it is submitted.")

    if estimate:
        built = flt(frappe.db.get_value("Cost Estimation", estimate, "total_estimated_cost"))
        print(f"\nRECONCILE  sheet {sheet_total:>14,.2f}   estimate {built:>14,.2f}   "
              f"difference {sheet_total - built:>12,.2f}")
    return estimate
