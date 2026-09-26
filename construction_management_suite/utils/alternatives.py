"""One material standing in for another.

A job is estimated with one brand of paint and built with whatever the merchant
had that week. That is not a departure from the plan — it is the same work, the
same quantity, the same money, bought from a different tin — and the module
used to treat it as two unrelated facts: the planned paint showing its full
quantity still to buy, the substitute showing as material nobody planned, and
a warning on every line that said so.

ERPNext already has the doctype for this. `Item Alternative` names an item, the
item that may stand in for it, and whether the swap runs both ways. Nothing in
this module read it. These helpers are the one place that does, so the purchase
check, the line message and the take-off cannot disagree about what counts as
a substitute.

Deliberately not inferred. An alternative exists because somebody said the two
are interchangeable for this purpose; guessing it from item groups or names
would quietly net off materials that are nothing like each other.
"""

import frappe


def stand_in_map():
    """Every substitute, mapped to the item it stands in for.

    `{substitute_code: original_code}`. A two-way alternative is both, so it
    appears in each direction — which is what "two way" means: either tin
    satisfies a plan written for the other.

    Cached per request. The table is small, it changes when somebody adds an
    alternative, and the take-off walks thousands of rows through this.
    """
    # `frappe.local` is a werkzeug proxy, so it is read with getattr, not
    # through a __dict__ it does not have.
    cached = getattr(frappe.local, "_cms_stand_in", None)
    if cached is not None:
        return cached

    mapping = {}
    for row in frappe.get_all(
        "Item Alternative",
        fields=["item_code", "alternative_item_code", "two_way"],
    ):
        if not (row.item_code and row.alternative_item_code):
            continue
        mapping.setdefault(row.alternative_item_code, row.item_code)
        if row.two_way:
            mapping.setdefault(row.item_code, row.alternative_item_code)

    frappe.local._cms_stand_in = mapping
    return mapping


def stands_in_for(item_code, planned):
    """Which planned item, if any, this one is allowed to stand in for.

    `planned` is whatever the caller counts as planned — the take-off's item
    codes, or the codes planned for one line of work. Returns None when the
    item is planned in its own right, because then it is not standing in for
    anything and nothing should be folded.

    One hop only. If A may be replaced by B and B by C, buying C does not
    satisfy a plan written for A — that chain is somebody's assumption, not
    something they stated, and a take-off that follows it silently orders
    against the wrong line.
    """
    if not item_code or item_code in planned:
        return None
    original = stand_in_map().get(item_code)
    return original if original in planned else None
