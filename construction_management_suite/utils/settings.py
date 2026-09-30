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


def company_scoped_rates():
    """Is the rate library private to the company that owns each analysis?

    On by default. An analysis carries a company, and two builders sharing a
    site keep their own labour, plant and material rates — so costing one
    company's work from the other's library is wrong, silently and by exactly
    the difference between them. `is_default` makes it worse: it is a flag on
    the analysis, not a flag per company, so whichever company ticks it first
    decides the other's cost. A single-company site, or one deliberately
    keeping one shared library, turns this off.
    """
    return bool(cms_setting("scope_rate_analysis_by_company", 1))


def service_materials_allowed():
    """May a material be a service Item on this site?

    Off by default, and the module's model holds: work is a service Item, a
    material is a stock Item, and a store issues the second to build the first.

    On for a contractor who buys material against the job and expenses it at
    the purchase invoice without ever holding stock. Then a material is a
    service Item too, ERPNext stops demanding a warehouse on every order line,
    and nothing is valued — which is the point, because there is nothing to
    value.

    It relaxes a requirement rather than reversing it: both kinds may sit in
    the same item master. A site that stocks cement and expenses paint is a
    normal state, not a mistake, so nothing here forces one or the other.

    What no setting can change is that stock cannot be issued where none is
    held. A Material Consumption Entry posts a Stock Entry, and a service Item
    cannot be in one — for those materials the cost is already in the books,
    booked by the purchase invoice.
    """
    return bool(cms_setting("allow_service_materials", 0))


def enforce(action, message, title=None):
    """Apply one of the three levels. Returns True when it blocked."""
    if action == "Stop":
        frappe.throw(message, title=title)
    if action == "Warn":
        frappe.msgprint(message, title=title, indicator="orange")
    return False


def enforce_setting(fieldname, message, title=None, default="Warn"):
    return enforce(action_for(fieldname, default), message, title)
