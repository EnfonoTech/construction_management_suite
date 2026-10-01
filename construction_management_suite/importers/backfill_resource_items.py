"""Give an Item to every resource that has only a description.

The take-off keys on the Item. A labour gang, a plant hire or a subcontract
priced inside a rate analysis with no `resource_item` is therefore invisible to
every per-work figure: it cannot be tracked in Material Position, it cannot be
forecast or requested, and it cannot be picked on a subcontract agreement.
Material rows have always named one; the other four kinds never did.

Analyses imported from now on name Items for every resource. This is for the
ones already on a site.

    bench --site <site> execute \
        construction_management_suite.importers.backfill_resource_items.run

    bench --site <site> execute \
        construction_management_suite.importers.backfill_resource_items.run \
        --kwargs "{'dry': 0}"

`dry=1` is the default and writes nothing.

Two things it touches, and it has to be both:

* the **live** resource rows, which the pickers and the rate library read;
* the **frozen** `rate_build_up` on every priced line, which is what the
  take-off actually walks. A snapshot is taken when a line is priced, so
  filling the analysis alone leaves every existing estimate unchanged — the
  whole point of the snapshot being that a signed bill does not move.

Nothing is renamed and nothing is re-costed. Where a resource already names an
Item it is left alone, and where an Item of that name already exists it is
reused rather than duplicated.
"""

import json

import frappe
from frappe.utils import cint

# Material is excluded on purpose: those rows have always named an Item, and a
# material with none is a defect the module already refuses to approve.
KINDS = ("Labour", "Equipment", "Subcontract", "Overhead")


def _code_for(description):
    from construction_management_suite.importers.misk_mawallah import item_code_for

    return item_code_for(description)


def _ensure_item(code, description, kind, log):
    """A service Item for this resource, filed by what it is."""
    from construction_management_suite.importers.misk_mawallah import _item_group

    # `frappe.db.exists` compares case-insensitively, so asking for
    # "Excavation Contract" finds "Excavation contract" and reports it there.
    # Return the name it is ACTUALLY stored under: everything downstream — the
    # take-off key, the plan check, a dict lookup — compares exactly, and a
    # reference in the other case matches nothing.
    existing = frappe.db.get_value("Item", code, "name")
    if existing:
        log["reused"] += 1
        return existing

    frappe.get_doc({
        "doctype": "Item",
        "item_code": code,
        "item_name": description[:140],
        "description": description,
        "item_group": _item_group(kind),
        # Never stocked, whatever the site's setting says: nobody warehouses a
        # labour gang or a subcontracted package.
        "stock_uom": "Ls",
        "is_stock_item": 0,
        "is_purchase_item": 1,
    }).insert(ignore_permissions=True)
    log["created"] += 1
    return code


def _fix_snapshots(codes, dry, log):
    """Fill the same Items into the frozen build-ups the take-off walks.

    Matched on description, because that is the only thing a snapshot row and a
    live resource row reliably share — the snapshot has no link back to the row
    it came from.
    """
    for doctype in ("Cost Estimation Item", "BOQ Item"):
        if not frappe.db.has_column(doctype, "rate_build_up"):
            continue
        for row in frappe.get_all(
            doctype,
            filters={"rate_build_up": ("is", "set")},
            fields=["name", "rate_build_up"],
        ):
            try:
                frozen = json.loads(row.rate_build_up or "{}")
            except (ValueError, TypeError):
                continue
            resources = frozen.get("resources") or []
            touched = False
            for res in resources:
                if res.get("resource_item") or (res.get("type") not in KINDS):
                    continue
                code = codes.get((res.get("description") or "").strip().lower())
                if not code:
                    continue
                res["resource_item"] = code
                touched = True
                log["snapshot_rows"] += 1
            if touched:
                log["snapshots"] += 1
                if not dry:
                    frappe.db.set_value(
                        doctype, row.name, "rate_build_up",
                        json.dumps(frozen), update_modified=False,
                    )


def run(dry=1, kinds=None):
    """Report what is unnamed, and name it unless `dry`."""
    dry = cint(dry)
    wanted = tuple(k.strip() for k in kinds.split(",")) if kinds else KINDS

    rows = frappe.get_all(
        "Rate Analysis Resource",
        filters={"resource_type": ("in", wanted), "resource_item": ("in", (None, ""))},
        fields=["name", "parent", "resource_type", "description"],
        order_by="parent, idx",
    )
    rows = [r for r in rows if (r.description or "").strip()]

    log = {"rows": len(rows), "created": 0, "reused": 0, "linked": 0,
           "snapshots": 0, "snapshot_rows": 0, "no_description": 0}

    by_kind = {}
    for row in rows:
        by_kind[row.resource_type] = by_kind.get(row.resource_type, 0) + 1

    print(f"\n{'DRY RUN — nothing is written' if dry else 'BACKFILLING'}")
    print(f"resources with a description and no Item: {len(rows)}")
    for kind, n in sorted(by_kind.items(), key=lambda kv: -kv[1]):
        print(f"   {kind:12} {n:>4}")

    unnamed = frappe.db.count(
        "Rate Analysis Resource",
        {"resource_type": ("in", wanted), "resource_item": ("in", (None, "")),
         "description": ("in", (None, ""))},
    )
    if unnamed:
        log["no_description"] = unnamed
        print(f"   {'(no description)':12} {unnamed:>4}  — left alone, there is "
              f"nothing to name them from")

    codes = {}
    for row in rows:
        description = row.description.strip()
        key = description.lower()
        if key not in codes:
            code = _code_for(description)
            if not dry:
                code = _ensure_item(code, description, row.resource_type, log)
            elif frappe.db.get_value("Item", code, "name"):
                log["reused"] += 1
            else:
                log["created"] += 1
            codes[key] = code
        if not dry:
            # `update_modified=False` and a direct write: these rows are locked
            # behind submitted documents, and this is filling in a reference
            # that was always meant to be there, not changing what was priced.
            frappe.db.set_value("Rate Analysis Resource", row.name,
                                "resource_item", codes[key], update_modified=False)
        log["linked"] += 1

    _fix_snapshots(codes, dry, log)

    print(f"\nItems: {log['created']} to create, {log['reused']} already there")
    print(f"resource rows linked : {log['linked']}")
    print(f"frozen build-ups     : {log['snapshot_rows']} row(s) across "
          f"{log['snapshots']} priced line(s)")
    if dry:
        print("\nNothing was written. Re-run with --kwargs \"{'dry': 0}\".")
    else:
        frappe.db.commit()
    return log
