from frappe import _


def get_data():
    """The request this raised.

    The generated document points back here with a Link; this document holds
    only its name as text. Linking both ways made us a dependency of the
    document we created, so cancelling the invoice cancelled the certificate.
    """
    return {
        "fieldname": "cms_site_request_ref",
        "transactions": [
            {"label": _("Procurement"), "items": ['Material Request']},
        ],
    }
