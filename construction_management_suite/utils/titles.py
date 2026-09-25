"""Naming a document so a list of them can be read.

Every title field in this app was mandatory and typed by hand, which produces
either a wall of "test" or a wall of the same project name. These build a
descriptive title from what the document already knows, only when the user has
not written one — so a client's own reference always wins.
"""

import frappe
from frappe.utils import formatdate


def set_auto_title(doc, fieldname, parts):
    """Fill a title from `parts`, skipping blanks. Never overwrites."""
    if doc.get(fieldname):
        return
    text = " — ".join(str(p).strip() for p in parts if p and str(p).strip())
    if text:
        doc.set(fieldname, text[:140])


def month_of(date):
    """'Aug 2026' — the period a certificate or report covers."""
    if not date:
        return None
    return formatdate(date, "MMM yyyy")


def project_label(project):
    """The project's name rather than its id, when it has one worth reading."""
    if not project:
        return None
    name = frappe.db.get_value("Project", project, "project_name")
    return name or project


def short_date(date):
    """'23 Sep 26' — enough to tell two entries apart, short enough for a list.

    The year stays: a job runs for three of them, and '23 Sep' on a document
    raised in a previous one is a trap rather than a saving.
    """
    if not date:
        return None
    return formatdate(date, "d MMM yy")
