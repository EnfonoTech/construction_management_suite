import frappe


def execute():
    """Forget the two tax templates the module used to name for the whole site.

    A tax template carries accounts, and accounts belong to a company, so one
    site-wide template is wrong the moment a second company exists — and it did
    exactly that here: an agreement for one company went out carrying the
    other's VAT. ERPNext already keeps a default per company on the template
    itself, which is what the four documents read now.

    The fields are gone from the doctype; their values stay in `tabSingles`
    until something removes them, where they would confuse the next person to
    look at the table.
    """
    frappe.db.delete(
        "Singles",
        {
            "doctype": "Construction Settings",
            "field": ("in", ("sales_taxes_template", "purchase_taxes_template")),
        },
    )
    frappe.clear_document_cache("Construction Settings", "Construction Settings")
