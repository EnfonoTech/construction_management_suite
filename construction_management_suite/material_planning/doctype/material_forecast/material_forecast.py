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
    def before_submit(self):
        # before, not on_submit: on_submit runs after the row is written.
        # A submitted forecast read "Draft" because nothing set this.
        self.status = "Approved"

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

        Every figure here — quantity, rate — is already computed by the take-off
        that the BOQ Resource Analysis report and the consumption check read.
        Retyping it was three places to disagree. The quantity it proposes stays
        editable: a take-off is a starting point, not a verdict.
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
            outstanding = flt(line["boq_qty"]) - flt(elsewhere.get(line["item_code"]))
            if outstanding <= 0.0001:
                skipped += 1
                continue
            row = rows.get(line["item_code"])
            values = {
                "uom": line["uom"],
                "net_qty_required": outstanding,
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
            item.already_ordered_qty = self._covered_qty(item.item_code)
            # net_qty_required is left as it stands: the take-off proposes it and
            # the planner may have changed it.
            # What you can actually place on an order — a whole-number UOM will
            # not accept a fraction.
            item.qty_to_order = orderable_qty(
                max(0, flt(item.net_qty_required) - flt(item.already_ordered_qty)), item.uom
            )
            item.estimated_value = flt(item.qty_to_order) * flt(item.estimated_rate)
        self.total_forecast_qty_value = sum(flt(i.estimated_value) for i in self.items)
        self.set_procurement_status()

    def set_procurement_status(self):
        """Say how much of the plan has actually been asked for.

        Partially Procured and Fully Procured were options nothing ever set, so
        a list of forecasts could not tell a plan that had been acted on from
        one nobody had touched.
        """
        if self.docstatus != 1 or self.status == "Cancelled":
            return
        outstanding = sum(flt(i.qty_to_order) for i in self.items)
        covered = sum(flt(i.already_ordered_qty) for i in self.items)
        if self.items and outstanding <= 0.0001:
            self.status = "Fully Procured"
        elif covered > 0.0001:
            self.status = "Partially Procured"
        else:
            self.status = "Approved"

    @frappe.whitelist()
    def refresh_coverage(self):
        """Recompute coverage on a forecast already submitted.

        recalculate() runs in validate(), which Frappe skips on a submitted
        document, so the figures on screen were whatever they were at
        submission until the nightly job caught up. Written straight through,
        the way Project Budget refreshes its actuals.
        """
        self.recalculate()
        if self.docstatus == 1:
            self.db_update()
            for item in self.items:
                item.db_update()
        else:
            self.save()
        frappe.msgprint(_("Coverage refreshed"), alert=True)
        return self.status

    def _covered_qty(self, item_code):
        """What is already on its way for this item on this project.

        Open Material Request quantity plus submitted Purchase Order quantity.
        A request's own ordered_qty comes off, or a request and the order it
        became are counted twice and the forecast asks for nothing.

        Counting requests as well as orders is what stops the same forecast
        being raised twice: netting on orders alone left qty_to_order untouched
        until a buyer got round to the purchase order.

        Scoped by required-by date when the forecast states a period — a
        forecast for October should not net off what September needs — and to
        the whole project when it does not. The date is the one on the line,
        not the document: material is ordered ahead of when it is wanted.
        """
        if not (item_code and self.project):
            return 0.0

        window = "" if not (self.from_date and self.to_date) else \
            " AND i.schedule_date BETWEEN %(from_date)s AND %(to_date)s"
        params = {
            "item_code": item_code, "project": self.project,
            "from_date": self.from_date, "to_date": self.to_date,
        }

        requested = frappe.db.sql(
            f"""
            SELECT SUM(GREATEST(i.qty - IFNULL(i.ordered_qty, 0), 0)) AS total
            FROM `tabMaterial Request Item` i
            JOIN `tabMaterial Request` m ON m.name = i.parent
            WHERE i.item_code = %(item_code)s
              AND i.project = %(project)s
              AND m.docstatus = 1
              AND m.status NOT IN ('Stopped', 'Cancelled')
              {window}
            """,
            params,
        )
        ordered = frappe.db.sql(
            f"""
            SELECT SUM(i.qty) AS total
            FROM `tabPurchase Order Item` i
            JOIN `tabPurchase Order` o ON o.name = i.parent
            WHERE i.item_code = %(item_code)s
              AND i.project = %(project)s
              AND o.docstatus = 1
              AND o.status NOT IN ('Completed', 'Cancelled')
              {window}
            """,
            params,
        )
        return flt(requested[0][0]) + flt(ordered[0][0])
