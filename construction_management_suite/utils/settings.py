"""One place to ask what the module is configured to do, and one way to act on it.

Every check in the app reads its severity from Construction Settings rather than
deciding for itself, so a site can tighten or loosen the whole module without a
code change. The three levels are ERPNext's own, from Budget: Ignore, Warn, Stop.
"""

import frappe
from frappe import _

SETTINGS = "Construction Settings"


def cms_settings():
    """Cached — these are read inside per-row loops."""
    return frappe.get_cached_doc(SETTINGS)


def cms_setting(fieldname, default=None):
    value = cms_settings().get(fieldname)
    # A Single that has never been saved returns None for everything, and an
    # unset Select is the empty string. Both mean "use the default".
    return default if value in (None, "") else value


def action_for(fieldname, default="Warn"):
    action = cms_setting(fieldname, default)
    return action if action in ("Ignore", "Warn", "Stop") else default


def enforce(action, message, title=None):
    """Apply one of the three levels. Returns True when it blocked."""
    if action == "Stop":
        frappe.throw(message, title=title)
    if action == "Warn":
        frappe.msgprint(message, title=title, indicator="orange")
    return False


def enforce_setting(fieldname, message, title=None, default="Warn"):
    return enforce(action_for(fieldname, default), message, title)
