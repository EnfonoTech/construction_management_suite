import frappe
from frappe.model.document import Document


class ConstructionSettings(Document):
    def on_update(self):
        # clear_document_cache, not clear_cache(doctype=...): the latter clears
        # the meta, not the cached document, so every caller reading this through
        # frappe.get_cached_doc kept serving the old severities until the worker
        # was recycled. A setting that does not take effect when you save it is
        # worse than no setting.
        frappe.clear_document_cache(self.doctype, self.name)
