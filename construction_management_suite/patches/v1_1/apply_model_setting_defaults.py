import frappe


def execute():
    """Write defaults for the model-conformance settings added in this release.

    A field's `default` applies only when a document is created, and
    Construction Settings was created long ago — so a new Select lands empty on
    an existing site and `action_for` falls back to its hard-coded default
    rather than anything the form shows.

    `frappe.db.has_column` is not usable here: this is a Single, and its values
    live in `tabSingles`.
    """
    defaults = {
        "work_item_type_action": "Warn",
        "material_resource_action": "Stop",
        "missing_rate_analysis_action": "Warn",
        "no_estimate_action": "Stop",
    }

    meta = frappe.get_meta("Construction Settings")
    settings = frappe.get_single("Construction Settings")
    changed = False
    for fieldname, value in defaults.items():
        if not meta.get_field(fieldname):
            continue
        if settings.get(fieldname):
            continue
        settings.set(fieldname, value)
        changed = True

    if changed:
        settings.flags.ignore_permissions = True
        settings.save()
        # A Single caches as a document; clearing the meta alone leaves the
        # stale values in place for every reader.
        frappe.clear_document_cache("Construction Settings", "Construction Settings")
