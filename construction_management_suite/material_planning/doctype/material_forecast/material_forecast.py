import frappe
from frappe.model.document import Document
from frappe.utils import flt


class MaterialForecast(Document):
    def validate(self):
        self.recalculate()

    def recalculate(self):
        """Work out what still needs ordering, and what that will cost.

        Called on every save and again nightly by the scheduler, so a forecast is
        correct the moment it is entered rather than only after the job has run.
        """
        for item in self.items:
            item.already_ordered_qty = self._ordered_qty(item.item_code)
            item.net_qty_required = flt(item.boq_qty) * (1 + flt(item.waste_factor) / 100)
            item.qty_to_order = max(0, flt(item.net_qty_required) - flt(item.already_ordered_qty))
            item.estimated_value = flt(item.qty_to_order) * flt(item.estimated_rate)
        self.total_forecast_qty_value = sum(flt(i.estimated_value) for i in self.items)

    def _ordered_qty(self, item_code):
        """Quantity already on open Purchase Orders for this item on this project."""
        if not (item_code and self.project):
            return 0.0
        ordered = frappe.db.sql(
            """
            SELECT SUM(poi.qty) AS total
            FROM `tabPurchase Order Item` poi
            JOIN `tabPurchase Order` po ON po.name = poi.parent
            WHERE poi.item_code = %s
              AND po.project = %s
              AND po.docstatus = 1
              AND po.status NOT IN ('Completed', 'Cancelled')
            """,
            (item_code, self.project),
            as_dict=True,
        )
        return flt((ordered[0] or {}).get("total", 0))
