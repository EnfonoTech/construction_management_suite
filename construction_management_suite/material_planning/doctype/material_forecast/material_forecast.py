import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt
from construction_management_suite.utils.accounting import get_warehouse
from construction_management_suite.utils.billing import orderable_qty
from construction_management_suite.utils.validations import (
    require_estimate,
    validate_item_kinds,
    validate_project_company,
)
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
        require_estimate(self.project, _("material can be forecast for it"))
        validate_item_kinds(self.items)
        self.recalculate()
        self.set_default_warehouse()
        self.set_work_numbers()

    def set_default_warehouse(self):
        """The job's store on every row that does not name one.

        It travels: the Material Request raised from this forecast takes each
        row's warehouse, so filling it here is what stops a buyer being asked
        where to deliver on a job that has only one store.
        """
        store = get_warehouse(self.project, self.company)
        if not store:
            return
        for row in self.items:
            if not row.warehouse:
                row.warehouse = store

    def set_work_numbers(self):
        """Fill the bill number beside each work item. Display only."""
        from construction_management_suite.material_planning.doctype.material_consumption_entry.material_consumption_entry import (
            work_no_map,
        )

        project = self.get("project") or self.get("to_project")
        numbers = work_no_map(project) if project else {}
        for row in self.items:
            row.work_no = numbers.get(row.work_item)


    @frappe.whitelist()
    def get_items_from_estimate(self):
        """Build the forecast from what the priced work is costed to consume.

        One row per material per work, not per material: the same cement sits
        under the substructure, the blockwork mortar and the plaster, and a
        single cement row could never say which of them still needs buying.
        This is also how the client's own sheet reads — cement appears again
        under every trade that uses it.

        The quantity it proposes stays editable: a take-off is a starting
        point, not a verdict.
        """
        from construction_management_suite.material_planning.doctype.material_consumption_entry.material_consumption_entry import (
            take_off_by_line,
        )

        if not self.project:
            frappe.throw(_("Choose the project this forecast is for"))

        # A job is normally forecast in stages, so what other submitted
        # forecasts already cover is netted off — per work, so planning the
        # plaster does not cancel out the cement the blockwork still needs.
        planned = frappe.db.sql(
            """
            SELECT i.item_code AS code, i.work_item AS ref, SUM(i.net_qty_required) AS qty
            FROM `tabMaterial Forecast Item` i
            JOIN `tabMaterial Forecast` f ON f.name = i.parent
            WHERE f.project = %(p)s AND f.docstatus = 1 AND f.name != %(n)s
            GROUP BY i.item_code, i.work_item
            """,
            {"p": self.project, "n": self.name or ""},
            as_dict=True,
        )
        elsewhere = {(r.code, r.ref): flt(r.qty) for r in planned}

        rows = {(i.item_code, i.work_item): i for i in self.items if i.item_code}
        added = updated = skipped = 0
        for entry in sorted(
            take_off_by_line(self.project).values(),
            key=lambda e: (str(e["work_item"] or ""), e["item_code"]),
        ):
            key = (entry["item_code"], entry["work_item"])
            outstanding = flt(entry["qty"]) - flt(elsewhere.get(key))
            if outstanding <= 0.0001:
                skipped += 1
                continue
            values = {
                "uom": entry["uom"],
                "net_qty_required": outstanding,
                "estimated_rate": entry["estimated_rate"],
                "work_item": entry["work_item"],
            }
            row = rows.get(key)
            if row:
                row.update(values)
                updated += 1
            else:
                self.append("items", dict(item_code=entry["item_code"], **values))
                added += 1
        self.recalculate()
        return {"added": added, "updated": updated, "already_planned": skipped}

    def recalculate(self):
        """Work out what still needs ordering, and what that will cost.

        Called on every save and again nightly by the scheduler, so a forecast
        is correct the moment it is entered rather than only after the job has
        run.
        """
        covered = self.allocate_coverage()
        for item in self.items:
            # Carried on the row so the form can round exactly as this does.
            item.uom_must_be_whole = 1 if (
                item.uom and frappe.db.get_value("UOM", item.uom, "must_be_whole_number")
            ) else 0
            item.already_ordered_qty = flt(covered.get(item.name))
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

    def allocate_coverage(self):
        """Share each material's open requests and orders across its rows.

        Coverage is known per material — a purchase order names cement, not the
        plaster it is for — while the forecast now carries a row per material
        per work. Counting the whole coverage against every row made 8,060 bags
        of cement cover 4,704 for the substructure, 1,499 for the blockwork and
        1,856 for the plaster all at once, and the job read as fully bought.

        Drawn down in row order, so the total offered across the rows is the
        material's real outstanding quantity.
        """
        pool = {}
        for item in self.items:
            if item.item_code and item.item_code not in pool:
                pool[item.item_code] = self._covered_qty(item.item_code)

        allocated = {}
        for item in self.items:
            need = flt(item.net_qty_required)
            available = flt(pool.get(item.item_code))
            take = min(need, available) if available > 0 else 0
            allocated[item.name] = take
            if take:
                pool[item.item_code] = available - take
        return allocated

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
