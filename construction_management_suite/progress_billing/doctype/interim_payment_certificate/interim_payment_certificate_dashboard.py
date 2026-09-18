from frappe import _


def get_data():
    """What this certificate raised, and what it was billed against.

    The generated document points back here with a Link; this document holds
    only its name as text. Linking both ways made us a dependency of the
    document we created, so cancelling the invoice cancelled the certificate.
    """
    return {
        "fieldname": "cms_ipc_ref",
        "transactions": [
            {"label": _("Billing"), "items": ['Sales Invoice']},
        ],
    }
