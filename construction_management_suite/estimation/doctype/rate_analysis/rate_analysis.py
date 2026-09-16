import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now, nowdate

from construction_management_suite.api.boq import get_item_rate


class RateAnalysisInUse(frappe.ValidationError):
	pass


# Changing any of these changes what a priced line was costed from, so they
# freeze once the analysis is behind a submitted document.
LOCKED_FIELDS = ("item_code", "uom", "company", "currency", "output_qty")

# The fields on a resource row that actually move money. Compared one by one
# rather than with is_child_table_same(), which compares every field including
# `description` and `uom` — both of which carry fetch_from with no
# fetch_if_empty, so Frappe re-fetches them on every save. Renaming an Item
# would otherwise make an analysis permanently unsaveable, with no way out
# through the UI.
RESOURCE_COSTED_FIELDS = ("resource_type", "resource_item", "qty", "rate", "waste_factor")

LEFT_DRAFT = ("Approved", "Obsolete")


class RateAnalysis(Document):
	def onload(self):
		self.set_onload("lock_state", self.get_lock_state())

	def validate(self):
		self.set_missing_defaults()
		self.validate_locked_content()
		self.validate_rate_basis()
		self.validate_active_and_default()
		self.calculate_resources()
		self.calculate_totals()

	def on_update(self):
		self.clear_other_defaults()

	def set_missing_defaults(self):
		if not self.status:
			self.status = "Draft"
		if not self.rate_basis:
			self.rate_basis = "Manual"

	# ----- Calculations -----
	# Left exactly as they were. tests/server.py calls these directly, bypassing
	# validate(), so the browser/server parity check stays meaningful only while
	# no locking or costing logic leaks into them.

	def calculate_resources(self):
		for res in self.resources:
			res.amount = flt(res.qty) * flt(res.rate)
			res.net_amount = flt(res.amount) * (1 + flt(res.waste_factor) / 100)

	def calculate_totals(self):
		totals = {"Material": 0, "Labour": 0, "Equipment": 0, "Subcontract": 0, "Overhead": 0}
		for res in self.resources:
			rt = res.resource_type
			if rt in totals:
				totals[rt] += flt(res.net_amount)

		self.total_material_cost = totals["Material"]
		self.total_labour_cost = totals["Labour"]
		self.total_equipment_cost = totals["Equipment"]
		self.total_subcontract_cost = totals["Subcontract"]
		self.total_overhead_cost = totals["Overhead"]
		self.total_cost = sum(totals.values())
		output = flt(self.output_qty) or 1
		self.rate_per_unit = self.total_cost / output

	# ----- Locking -----

	def submitted_references(self):
		"""Submitted BOQs and Cost Estimations that priced a line from this.

		`method="Cancel"` is what restricts get_linked_docs to docstatus 1, and
		it resolves the child-table hop (BOQ Item -> BOQ) that a plain
		frappe.db.exists on the child table would miss.
		"""
		if self.is_new():
			return []
		if getattr(self.flags, "_submitted_refs", None) is not None:
			return self.flags._submitted_refs

		from frappe.model.delete_doc import get_linked_docs

		seen, refs = set(), []
		for link in get_linked_docs(self, method="Cancel") or []:
			key = (link.get("reference_doctype"), link.get("reference_docname"))
			if key in seen or not key[1]:
				continue
			seen.add(key)
			refs.append({"doctype": key[0], "name": key[1]})

		self.flags._submitted_refs = refs
		return refs

	def get_lock_state(self):
		refs = self.submitted_references()
		before = self.get_doc_before_save()
		# The STORED status, not the one being saved. Otherwise setting Obsolete
		# and editing the resources in the same save would slip through.
		stored = (before.status if before else self.status) or "Draft"
		successor = (
			frappe.db.get_value("Rate Analysis", {"previous_version": self.name}, "name")
			if not self.is_new()
			else None
		)
		return {
			"locked": stored in LEFT_DRAFT and bool(refs),
			"references": refs[:5],
			"reference_count": len(refs),
			"successor": successor,
			"locked_fields": list(LOCKED_FIELDS) + ["resources"],
		}

	def validate_locked_content(self):
		if self.is_new():
			return
		before = self.get_doc_before_save()
		if not before:
			return
		# Obsolete stays locked as well as Approved. If retiring an analysis
		# unlocked it, the bypass would be: mark Obsolete, edit, mark Approved.
		if (before.status or "Draft") not in LEFT_DRAFT:
			return
		refs = self.submitted_references()
		if not refs:
			return

		changed = [f for f in LOCKED_FIELDS if self.get(f) != before.get(f)]
		if self.resources_changed(before):
			changed.append("resources")
		if not changed:
			return

		used = ", ".join(r["name"] for r in refs[:3])
		if len(refs) > 3:
			used += _(" and {0} more").format(len(refs) - 3)
		labels = ", ".join(_(self.meta.get_label(f)) if f != "resources" else _("Resources") for f in changed)
		frappe.throw(
			_(
				"{0} is priced into {1}, so its costing cannot be changed — those documents "
				"would lose the provenance of their rates.<br><br>Changed: <b>{2}</b>"
				"<br><br>Use <b>Actions &gt; New Version</b> to make a copy you can edit. "
				"Status, name and notes can still be changed here."
			).format(self.name, used, labels),
			title=_("Rate Analysis In Use"),
			exc=RateAnalysisInUse,
		)

	def resources_changed(self, before):
		old, new = before.get("resources") or [], self.resources or []
		if len(old) != len(new):
			return True
		for a, b in zip(old, new):
			for field in RESOURCE_COSTED_FIELDS:
				x, y = a.get(field), b.get(field)
				if isinstance(x, (int, float)) or isinstance(y, (int, float)):
					if flt(x, self.precision(field, b) or 6) != flt(y, self.precision(field, b) or 6):
						return True
				elif (x or None) != (y or None):
					return True
		return False

	# ----- Versioning -----

	@frappe.whitelist()
	def make_new_version(self):
		"""Copy this analysis to a fresh Draft so its costing can be changed."""
		existing = frappe.db.get_value(
			"Rate Analysis", {"previous_version": self.name, "status": "Draft"}, "name"
		)
		if existing:
			frappe.msgprint(
				_("A draft version of this analysis already exists: {0}").format(
					frappe.utils.get_link_to_form("Rate Analysis", existing)
				)
			)
			return existing

		# ignore_no_copy=False, because frappe.copy_doc defaults it to True and
		# would carry across the very fields marked no_copy.
		new = frappe.copy_doc(self, ignore_no_copy=False)
		new.status = "Draft"
		new.is_active = 1
		# Never inherited. Two analyses claiming to be the default for one item
		# is the one state clear_other_defaults() cannot resolve on its own.
		new.is_default = 0
		new.previous_version = self.name
		new.date = nowdate()
		new.last_cost_update = None
		new.insert()
		frappe.msgprint(
			_(
				"Created {0}. This version is untouched — when the new one is approved, "
				"tick <b>Is Default</b> on it, and untick <b>Is Active</b> here if you no "
				"longer want it offered."
			).format(frappe.utils.get_link_to_form("Rate Analysis", new.name))
		)
		return new.name

	# ----- Active / default -----

	def validate_active_and_default(self):
		"""Keep the two flags honest about what they claim.

		A retired analysis cannot be active, and nothing but a live, approved
		analysis can be the one an item is priced from by default. Enforced
		here rather than left to the user because `is_default` is what the
		pickers read — a default pointing at an obsolete build-up would quietly
		price new work from a rate nobody stands behind any more.
		"""
		if self.status == "Obsolete":
			self.is_active = 0
		if not self.is_active:
			self.is_default = 0
		if self.is_default and self.status != "Approved":
			frappe.throw(
				_("Only an Approved analysis can be the default for {0}.").format(
					self.item_code or _("an item")
				)
			)

	def clear_other_defaults(self):
		"""One default per item — set here, unset everywhere else.

		Done after the write rather than in validate() so the winner is already
		on disk: if this save later fails, nothing else has been demoted.
		"""
		if not self.is_default or not self.item_code:
			return
		others = frappe.get_all(
			"Rate Analysis",
			filters={"item_code": self.item_code, "is_default": 1, "name": ("!=", self.name)},
			pluck="name",
		)
		for name in others:
			frappe.db.set_value("Rate Analysis", name, "is_default", 0, update_modified=False)
		if others:
			frappe.msgprint(
				_("{0} is now the default analysis for {1}; {2} no longer is.").format(
					self.name, self.item_code, ", ".join(others)
				),
				alert=True,
			)

	# ----- Cost configuration -----

	def validate_rate_basis(self):
		if self.rate_basis != "Price List":
			return
		if not self.buying_price_list:
			frappe.throw(_("Select a Price List, or change the Rate Basis"))
		# No exchange-rate handling exists anywhere in this app; refusing is
		# honest, silently mixing currencies is not.
		pl_currency = frappe.db.get_value("Price List", self.buying_price_list, "currency")
		if pl_currency and self.currency and pl_currency != self.currency:
			frappe.throw(
				_("Price List {0} is in {1} but this analysis is in {2}.").format(
					self.buying_price_list, pl_currency, self.currency
				)
			)

	@frappe.whitelist()
	def update_cost(self):
		"""Re-read every item-linked resource rate from the configured basis."""
		if self.is_new():
			frappe.throw(_("Save the analysis before updating its costs"))
		if self.status != "Draft":
			frappe.throw(
				_(
					"Costs can only be updated on a Draft analysis. {0} is {1} — use "
					"<b>Actions &gt; New Version</b> to reprice it."
				).format(self.name, self.status),
				title=_("Not a Draft"),
			)
		basis = self.rate_basis or "Manual"
		if basis == "Manual":
			frappe.throw(
				_("Set a <b>Rate Basis</b> first. Manual means these rates are typed by hand.")
			)

		before = {"total_cost": flt(self.total_cost), "rate_per_unit": flt(self.rate_per_unit)}
		changed, missing, skipped = [], [], []

		for row in self.resources:
			# Catches NULL and the empty string, which live data contains.
			if not row.resource_item:
				skipped.append({"idx": row.idx, "description": row.description})
				continue
			found = get_item_rate(
				row.resource_item,
				company=self.company,
				basis=basis,
				price_list=self.buying_price_list,
			)
			new_rate = flt(found.get("rate"))
			if not new_rate:
				missing.append({"idx": row.idx, "resource_item": row.resource_item})
				continue
			precision = self.precision("rate", row) or 6
			if flt(row.rate, precision) == flt(new_rate, precision):
				continue
			changed.append(
				{
					"idx": row.idx,
					"resource_item": row.resource_item,
					"old_rate": flt(row.rate),
					"new_rate": new_rate,
					"source": found.get("source"),
				}
			)
			row.rate = new_rate

		if changed:
			self.last_cost_update = now()
			# save(), not db_update(): db_update skips on_update, writes no
			# Version row, and — the dangerous one — never clears the document
			# cache, so a get_cached_doc elsewhere in the same request would
			# freeze a stale build-up onto a freshly repriced line.
			self.save()

		return {
			"basis": basis,
			"price_list": self.buying_price_list,
			"changed": changed,
			"missing": missing,
			"skipped_no_item": skipped,
			"before": before,
			"after": {"total_cost": flt(self.total_cost), "rate_per_unit": flt(self.rate_per_unit)},
		}
