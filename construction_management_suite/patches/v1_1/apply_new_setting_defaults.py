"""Apply the defaults of settings added after the Single already existed.

A field's `default` is applied when a document is created. Construction Settings
was created before these fields existed, so they were added as empty columns and
the defaults never ran — and for a Check, 0 is indistinguishable from unset, so
no fallback can recover it either. `auto_create_project_cost_center` therefore
read 0 while claiming to default to 1, and every posting kept landing in the
company cost centre.

Applied once, and never over a value someone has already chosen: a setting the
user has saved is left alone because the Single carries a modified timestamp
from that save.
"""

import frappe

# fieldname -> value the field declares as its default
DEFAULTS = {
    "auto_create_project_cost_center": 1,
    "advance_recovery_threshold_percent": 75,
    "default_subcontract_retention_percent": 10,
    "purchase_rate_tolerance_percent": 10,
    "consumption_tolerance_percent": 10,
    "default_contingency_percent": 5,
}


def execute():
    if not frappe.db.exists("DocType", "Construction Settings"):
        return
    # A Single has no table of its own — its values live in `tabSingles` — so
    # has_column throws on it. The meta is the right question to ask.
    meta = frappe.get_meta("Construction Settings")
    applied = []
    for fieldname, value in DEFAULTS.items():
        if not meta.get_field(fieldname):
            continue
        current = frappe.db.get_single_value("Construction Settings", fieldname)
        # Only where nothing is set. A deliberate zero on a percentage is
        # indistinguishable from unset, so those are left as they are.
        if current in (None, "", 0) and not _was_set(fieldname):
            frappe.db.set_single_value("Construction Settings", fieldname, value)
            applied.append(f"{fieldname}={value}")
    if applied:
        print("Construction: applied defaults " + ", ".join(applied))


def _was_set(fieldname):
    """True if a version row shows somebody changed this field before."""
    rows = frappe.get_all(
        "Version",
        filters={"ref_doctype": "Construction Settings"},
        fields=["data"],
        limit=50,
    )
    return any(fieldname in (r.data or "") for r in rows)
