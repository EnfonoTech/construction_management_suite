import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate
from construction_management_suite.utils.settings import (
    action_for,
    cms_setting,
    enforce,
    enforce_setting,
)
from construction_management_suite.utils.validations import validate_project_company


COMPONENT_RATES = (
    "material_rate", "labour_rate", "equipment_rate", "subcontract_rate", "overhead_rate",
)


class BOQ(Document):
    # ----- Lifecycle -----

    def validate(self):
        validate_project_company(self)
        self.set_rate_source()
        self.set_currency_from_project()
        self.set_item_numbers()
        self.apply_named_rate_analysis()
        self.calculate_item_amounts()
        self.calculate_totals()
        self.validate_items()
        self.warn_priced_below_cost()
        self.capture_rate_build_ups()

    def before_submit(self):
        self.validate_analyses_approved()
        self.validate_minimum_margin()
        self.status = "Submitted"

    def on_submit(self):
        self._update_project_boq_link()

    def on_cancel(self):
        self.status = "Cancelled"
        self._update_project_boq_link()

    def on_update_after_submit(self):
        self.calculate_totals()

    def before_save(self):
        if not self.prepared_by:
            self.prepared_by = frappe.session.user

    # ----- Calculations -----

    def set_rate_source(self):
        """The module default, unless this bill says otherwise.

        Held on the document rather than read from the settings each time, so
        changing the site default later cannot silently reprice an old bill from
        a different source than the one it was quoted on.

        The Select carries no blank option, and Frappe fills a blank Select with
        its first option before any of this runs (create_new.get_static_default_value),
        so only a document built without new_doc ever reaches here empty. What a
        user sees is set by the form script on load; this is the fallback for
        the API, an import, or get_mapped_doc.
        """
        if not self.rate_source:
            self.rate_source = cms_setting("default_rate_source", "Rate Analysis")
        if self.rate_source == "Price List" and not self.selling_price_list:
            self.selling_price_list = cms_setting("default_selling_price_list")

    def set_currency_from_project(self):
        if self.project and not self.currency:
            project_currency = frappe.db.get_value("Project", self.project, "currency")
            if project_currency:
                self.currency = project_currency

    def set_item_numbers(self):
        """Give every new line a reference that survives the rows moving.

        `idx` renumbers the moment a row is inserted above, so a bill referred
        to in correspondence as item 2.4 quietly becomes 2.5. Numbers are
        assigned once and never revised — an inserted line takes the next free
        number in its section rather than pushing everything down.
        """
        used = {i.item_no for i in self.items if i.item_no}
        sections = []
        for i in self.items:
            key = i.boq_section or ""
            if key not in sections:
                sections.append(key)
        # Flat numbering while the bill has no sections; 1.1 / 2.3 once it does.
        sectioned = len(sections) > 1 or (sections and sections[0])

        counters = {}
        for i in self.items:
            if i.item_no:
                continue
            key = i.boq_section or ""
            prefix = "{0}.".format(sections.index(key) + 1) if sectioned else ""
            n = counters.get(key, 0)
            while True:
                n += 1
                candidate = "{0}{1}".format(prefix, n)
                if candidate not in used:
                    break
            counters[key] = n
            used.add(candidate)
            i.item_no = candidate

    def apply_named_rate_analysis(self):
        """Cost a line from the analysis it names when nothing was costed yet.

        Picking an analysis in the grid fills the five component rates from the
        form script, so a line built by hand arrives complete. Every other route
        in — the REST API, a data import, get_mapped_doc, a fixture — left
        `rate_analysis_ref` pointing at an analysis whose cost never landed, and
        capture_rate_build_ups then declined to freeze a build-up because the
        line's cost did not match the analysis. Cost Estimation has always done
        this on the server; the BOQ simply never did.

        Only fills when all five are zero. A hand-adjusted breakdown is a
        decision, and a BOQ keeps its own rates on purpose — refreshing them
        from the library on every save is exactly what check_rate_drift exists
        to avoid.
        """
        from construction_management_suite.api.boq import (
            get_rate_analysis_rates,
            get_selling_rate,
        )

        if self.rate_source == "Price List" and self.selling_price_list:
            for item in self.items:
                if flt(item.rate) or not item.item_code:
                    continue
                item.rate = flt(get_selling_rate(item.item_code, self.selling_price_list))

        for item in self.items:
            if not item.rate_analysis_ref:
                continue
            if any(flt(item.get(f)) for f in COMPONENT_RATES):
                continue
            if not frappe.db.exists("Rate Analysis", item.rate_analysis_ref):
                continue
            rates = get_rate_analysis_rates(item.rate_analysis_ref)
            for field in COMPONENT_RATES:
                item.set(field, rates[field])
            # The analysis sets the SELLING rate only when the bill is priced
            # from the rate library. Under Price List the rate is whatever was
            # agreed; under Manual it is typed. Cost is filled either way,
            # because that is what margin is measured against.
            if not flt(item.rate) and self.rate_source == "Rate Analysis":
                item.rate = rates["rate"]

    def warn_priced_below_cost(self):
        """Say it out loud when a line sells for less than it costs to build.

        Not an error — front-loading and loss-leading are real tactics — but
        with the margin now derived rather than typed, nothing else on the form
        announces it.
        """
        action = action_for("below_cost_action")
        if action == "Ignore" or self.docstatus != 0:
            return
        # At field precision. cost_rate is a sum of five floats, so a line whose
        # rate exactly equals its cost lands at 0.42 vs 0.42000000000000004 and
        # reads as a loss.
        under = []
        for i in self.items:
            dp = self.precision("rate", i) or 2
            if flt(i.cost_rate, dp) and flt(i.rate, dp) < flt(i.cost_rate, dp):
                under.append(i)
        if not under:
            return
        lines = "<br>".join(
            _("Row {0} ({1}): sells {2}, costs {3}").format(
                i.idx,
                i.item_code,
                frappe.format_value(flt(i.rate), {"fieldtype": "Currency"}, self),
                frappe.format_value(flt(i.cost_rate), {"fieldtype": "Currency"}, self),
            )
            for i in under[:10]
        )
        enforce(
            action,
            lines + ("<br>…" if len(under) > 10 else ""),
            title=_("{0} line(s) priced below cost").format(len(under)),
        )

    def calculate_item_amounts(self):
        for item in self.items:
            item.cost_rate = (
                flt(item.material_rate) + flt(item.labour_rate) + flt(item.equipment_rate)
                + flt(item.subcontract_rate) + flt(item.overhead_rate)
            )
            # Margin is reported, never applied. The rate is what the estimator
            # decided to sell the line for — driving it off a typed percentage
            # meant the only way to adjust one line was to solve for the
            # percentage that produced the rate you already had in mind.
            item.margin_percent = (
                (flt(item.rate) - flt(item.cost_rate)) / flt(item.cost_rate) * 100
                if flt(item.cost_rate) else 0
            )
            item.margin_amount = (flt(item.rate) - flt(item.cost_rate)) * flt(item.qty)
            item.amount = flt(item.qty) * flt(item.rate)
            item.material_amount = flt(item.qty) * flt(item.material_rate)
            item.labour_amount = flt(item.qty) * flt(item.labour_rate)
            item.equipment_amount = flt(item.qty) * flt(item.equipment_rate)
            item.subcontract_amount = flt(item.qty) * flt(item.subcontract_rate)
            item.overhead_amount = flt(item.qty) * flt(item.overhead_rate)
            item.variance_qty = flt(item.actual_qty) - flt(item.qty)
            item.variance_amount = flt(item.variance_qty) * flt(item.rate)

    def calculate_totals(self):
        self.total_material_amount = sum(flt(i.material_amount) for i in self.items)
        self.total_labour_amount = sum(flt(i.labour_amount) for i in self.items)
        self.total_equipment_amount = sum(flt(i.equipment_amount) for i in self.items)
        self.total_subcontract_amount = sum(flt(i.subcontract_amount) for i in self.items)
        self.total_overhead_amount = sum(flt(i.overhead_amount) for i in self.items)
        self.total_amount = sum(flt(i.amount) for i in self.items)
        self.total_cost_amount = sum(flt(i.qty) * flt(i.cost_rate) for i in self.items)
        self.effective_margin_percent = (
            (flt(self.total_amount) - flt(self.total_cost_amount)) / flt(self.total_cost_amount) * 100
            if flt(self.total_cost_amount) else 0
        )
        # The tender sum IS the sum of the priced lines. A percentage added
        # below the line meant a client could add up the Amount column and get
        # a different figure from the total on the same page — and the line
        # explaining the gap was labelled "Margin". Margin belongs in the rates.
        self.grand_total = flt(self.total_amount)

    def validate_items(self):
        for row in self.items:
            if flt(row.qty) <= 0:
                frappe.throw(_("Row {0}: Quantity must be greater than zero").format(row.idx))
            if flt(row.rate) < 0:
                frappe.throw(_("Row {0}: Rate cannot be negative").format(row.idx))

    def capture_rate_build_ups(self):
        """Freeze the analysis behind any line that names one and has no copy yet.

        Doing it here rather than in the buttons means every route in — picking
        an analysis on the row, the two Actions, the REST API, a data import —
        ends up with the same record.

        A line is only frozen when its cost still matches what the analysis says
        today. If they have already drifted apart, the build-up on file would
        not be the one this rate came from, and a build-up that misrepresents
        its own line is worse than none at all.
        """
        from construction_management_suite.api.boq import snapshot_rate_analysis

        for item in self.items:
            if not item.rate_analysis_ref or item.rate_build_up:
                continue
            if not frappe.db.exists("Rate Analysis", item.rate_analysis_ref):
                continue
            ra = frappe.get_cached_doc("Rate Analysis", item.rate_analysis_ref)
            if abs(flt(ra.rate_per_unit) - flt(item.cost_rate)) > 0.005:
                continue
            item.rate_build_up = snapshot_rate_analysis(ra)
            if not item.rate_applied_on:
                item.rate_applied_on = frappe.utils.now()

    def validate_analyses_approved(self):
        """A bill should not be signed off an analysis nobody has approved.

        Checked on submit rather than on save, so an estimator can price a draft
        from a work-in-progress build-up and get it approved before the bill goes
        out.
        """
        action = action_for("unapproved_analysis_action")
        if action == "Ignore":
            return
        bad = []
        for item in self.items:
            if not item.rate_analysis_ref:
                continue
            status = frappe.db.get_value("Rate Analysis", item.rate_analysis_ref, "status")
            if status and status != "Approved":
                bad.append((item, status))
        if not bad:
            return
        enforce(
            action,
            "<br>".join(
                _("Row {0} ({1}): {2} is {3}").format(
                    i.idx, i.item_code, i.rate_analysis_ref, st
                )
                for i, st in bad[:10]
            ),
            title=_("{0} line(s) priced from an unapproved analysis").format(len(bad)),
        )

    def validate_minimum_margin(self):
        """Refuse or flag a bill that does not clear the margin floor."""
        action = action_for("below_minimum_margin_action", "Ignore")
        floor = flt(cms_setting("minimum_margin_percent", 0))
        if action == "Ignore" or not floor:
            return
        # Nothing costed means nothing to judge — an unpriced bill is not a
        # thin-margin bill.
        if not flt(self.total_cost_amount):
            return
        if flt(self.effective_margin_percent) >= floor:
            return
        enforce(
            action,
            _("This bill makes {0}% over its cost. The floor is {1}%.").format(
                flt(self.effective_margin_percent, 2), floor
            ),
            title=_("Below the minimum margin"),
        )

    # ----- Winning the job -----

    @frappe.whitelist()
    def create_project(self):
        """Open a Project for a BOQ that was priced before the job was won.

        A bill is quoted at tender stage, when there is nothing to attach it to
        yet, so `project` stays blank until the client awards the work. This
        carries across only what the BOQ already knows; everything else is the
        project manager's to fill in.
        """
        if self.project:
            frappe.throw(
                _("This BOQ is already on project {0}").format(self.project)
            )

        project = frappe.new_doc("Project")
        project.update({
            "project_name": self.boq_title or self.name,
            "company": self.company,
            "customer": self.client,
            "currency": self.currency,
            "status": "Open",
            "expected_start_date": self.contract_date,
            "cms_contract_value": flt(self.grand_total),
            "cms_client_po": self.client_po,
        })
        project.insert(ignore_permissions=True)

        # db_set, not save: the BOQ may already be submitted — winning the work
        # is not a change to the priced content.
        self.db_set("project", project.name)
        frappe.msgprint(
            _("Created {0}").format(frappe.utils.get_link_to_form("Project", project.name)),
            alert=True,
        )
        return project.name

    # ----- Revision Workflow -----

    @frappe.whitelist()
    def create_revision(self):
        """Create a revised BOQ copying all items — increments revision_no."""
        if self.docstatus != 1:
            frappe.throw(_("BOQ must be submitted before creating a revision"))

        new_boq = frappe.copy_doc(self)
        new_boq.docstatus = 0
        new_boq.status = "Draft"
        new_boq.revision_no = flt(self.revision_no) + 1
        new_boq.is_revised = 1
        # A revision is not an amendment — `amended_from` is reserved by Frappe for
        # cancelled documents and would be rejected on insert.
        new_boq.amended_from = None
        new_boq.previous_boq = self.name
        new_boq.approved_date = None
        new_boq.insert(ignore_permissions=True)

        self.db_set("status", "Revised")
        frappe.msgprint(_("Revised BOQ {0} created").format(new_boq.name))
        return new_boq.name

    # ----- ERPNext Integration -----

    def _update_project_boq_link(self):
        """Store latest active BOQ reference on the ERPNext Project."""
        if self.project:
            frappe.db.set_value("Project", self.project, "notes", self._build_project_notes())

    def _build_project_notes(self):
        import re

        existing = frappe.db.get_value("Project", self.project, "notes") or ""
        marker = "<!-- cms_boq -->"
        block = (
            f"{marker}\nBOQ: {self.name} | Grand Total: {self.grand_total} "
            f"{self.currency}\n{marker}"
        )
        if marker in existing:
            # The block is delimited by a matched pair of markers.
            return re.sub(
                re.escape(marker) + ".*?" + re.escape(marker), block, existing, flags=re.DOTALL
            )
        return existing + block

    # ----- Template Import -----

    @frappe.whitelist()
    def import_from_template(self, template_name):
        """Populate items from a BOQ Template."""
        template = frappe.get_doc("BOQ Template", template_name)
        for t_item in template.items:
            self.append("items", {
                "item_code": t_item.item_code,
                "description": t_item.description,
                "uom": t_item.uom,
                "qty": t_item.qty,
                "rate": t_item.rate,
                "work_category": t_item.work_category,
                "boq_section": t_item.boq_section,
            })
        self.calculate_item_amounts()
        self.calculate_totals()
