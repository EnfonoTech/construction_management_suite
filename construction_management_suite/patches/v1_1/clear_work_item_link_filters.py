import frappe


def execute():
    """Take the link filter off the work reference so its query can run.

    `cms_work_item` carried a `link_filters` restricting the picker to service
    items. Frappe's `apply_link_field_filters` treats that as incompatible with
    a custom query: it calls the field's `get_query` with no arguments — which
    threw, because the callback expects the document — keeps only the filters
    it returns, discards the `query`, and replaces the function. The
    project-scoped query was therefore never called, and `project` was sent to
    the standard Item search: "Unknown column tabItem.project".

    The restriction has not been lost; `api.boq.work_items_for_project` applies
    it, along with the project's own lines of work.
    """
    # NULL, not "": the column is JSON with a check constraint, and an empty
    # string is not valid JSON — MariaDB refuses it.
    frappe.db.sql(
        """UPDATE `tabCustom Field` SET link_filters = NULL
           WHERE fieldname = 'cms_work_item' AND link_filters IS NOT NULL"""
    )
    frappe.clear_cache()
