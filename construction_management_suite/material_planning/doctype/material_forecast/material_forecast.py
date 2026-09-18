import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt
from construction_management_suite.utils.billing import orderable_qty
from construction_management_suite.utils.validations import validate_project_company
from construction_management_suite.utils.titles import (
    month_of,
    project_label,
    set_auto_title,
)


class MaterialForecast(Document):
    def before_cancel(self):
        # before, not on_cancel: on_cancel runs after the row is written.
        self.status = "Cancelled"

    def validate(self):
        set_auto_title(self, "forecast_title", [_("Forecast"), project_label(self.project), month_of(self.forecast_date) if self.get("forecast_date") else None])
        validate_project_company(self)
        self.recalculate()

    @frappe.whitelist()
    def get_items_from_boq(self):
        """Build the forecast from what the bills are priced to consume.

        Every figure here — quantity, waste, rate — is already computed by the
        take-off that the BOQ Resource Analysis report and the consumption check
        read. Retyping it was three places to disagree.
        """
        from construction_management_suite.material_planning.doctype.material_consumption_entry.material_consumption_entry import (
            take_off_detail,
        )

        if not self.project:
            frappe.throw(_("Choose the project this forecast is for"))

        # A job is normally forecast in stages, so what other submitted
        # forecasts already cover is netted off — otherwise the second forecast
        # asks for the whole job again and the material is ordered twice.
        planned = frappe.db.sql(
            """
            SELECT i.item_code AS code, SUM(i.net_qty_required) AS qty
            FROM `tabMaterial Forecast Item` i
            JOIN `tabMaterial Forecast` f ON f.name = i.parent
            WHERE f.project = %(p)s AND f.docstatus = 1 AND f.name != %(n)s
            GROUP BY i.item_code
            """,
            {"p": self.project, "n": self.name or ""},
            as_dict=True,
        )
        elsewhere = {r.code: flt(r.qty) for r in planned}

        rows = {i.item_code: i for i in self.items if i.item_code}
        added = updated = skipped = 0
        for line in take_off_detail(self.project, boq=self.boq_ref):
            # net_qty_required is boq_qty plus waste, so take the already
            # planned quantity off the base before waste is applied again.
            waste = 1 + flt(line["waste_factor"]) / 100
            outstanding = flt(line["boq_qty"]) - (flt(elsewhere.get(line["item_code"])) / waste)
            if outstanding <= 0.0001:
                skipped += 1
                continue
            row = rows.get(line["item_code"])
            values = {
                "uom": line["uom"],
                "boq_qty": outstanding,
                "waste_factor": line["waste_factor"],
                "estimated_rate": line["estimated_rate"],
                "boq_items": ", ".join(line["boq_items"])[:140],
            }
            if row:
                row.update(values)
                updated += 1
            else:
                self.append("items", dict(item_code=line["item_code"], **values))
                added += 1
        self.recalculate()
        return {"added": added, "updated": updated, "already_planned": skipped}

    def recalculate(self):
        """Work out what still needs ordering, and what that will cost.

        Called on every save and again nightly by the scheduler, so a forecast is
        correct the moment it is entered rather than only after the job has run.
        """
        for item in self.items:
            # Carried on the row so the form can round exactly as this does.
            item.uom_must_be_whole = 1 if (
                item.uom and frappe.db.get_value("UOM", item.uom, "must_be_whole_number")
            ) else 0
            item.already_ordered_qty = self._ordered_qty(item.item_code)
            item.net_qty_required = flt(item.boq_qty) * (1 + flt(item.waste_factor) / 100)
            # What you can actually place on an order — a whole-number UOM will
            # not accept the fraction a waste factor produces.
            item.qty_to_order = orderable_qty(
                max(0, flt(item.net_qty_required) - flt(item.already_ordered_qty)), item.uom
            )
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
