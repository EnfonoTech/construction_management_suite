import frappe


def execute():
    """Default for `no_estimate_action`, added after the first defaults patch ran.

    A patch executes once. `apply_model_setting_defaults` had already run on
    sites migrated earlier in this release, so a key added to it afterwards
    would never reach them — hence a second patch rather than an edit.
    """
    if not frappe.get_meta("Construction Settings").get_field("no_estimate_action"):
        return
    settings = frappe.get_single("Construction Settings")
    if settings.get("no_estimate_action"):
        return
    settings.no_estimate_action = "Stop"
    settings.flags.ignore_permissions = True
    settings.save()
    frappe.clear_document_cache("Construction Settings", "Construction Settings")
