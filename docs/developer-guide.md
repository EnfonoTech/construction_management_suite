# Construction Management Suite — Developer Guide

*As of 2026-09-24. Published copy: https://claude.ai/artifact/TRdnQDrLhp4xwzkp5FSHUu*

A Frappe v15 / ERPNext v15 application: 8 modules, 37 doctypes, one whitelisted API module, and a deliberate rule that it measures while ERPNext accounts.

## Architecture

The app sits on top of ERPNext and never replaces its accounting. Construction documents **measure and certify**; every figure that must reach the ledger gets there by generating a standard ERPNext document and linking to it.

### Modules

`modules.txt` declares eight. Note **Construction Setup** — not `Setup`. Frappe resolves a Module Def to an app **by name across the whole bench**, so a module called `Setup` binds to ERPNext's and `bench migrate` dies with `ModuleNotFoundError: No module named 'erpnext.setup.doctype.construction_settings'`.

| Module | Doctypes | Purpose |
| --- | --- | --- |
| BOQ Management | BOQ, BOQ Template, Variation Order | The priced contract |
| Estimation | Rate Analysis, Cost Estimation | Unit-rate build-ups and the cost plan |
| Project Costing | Project Budget, Cost Code | Budget against actual and committed |
| Site Management | Daily Site Report, Site Material Request | Field records |
| Progress Billing | Interim Payment Certificate, Retention Release | Client billing |
| Subcontractor Management | Subcontract Agreement, Work Order, Payment Certificate | The buy side |
| Material Planning | Material Forecast, Site Transfer, Material Consumption Entry | Take-off to issue |
| Construction Setup | Construction Settings | One Single holding every policy |

37 doctypes in all: 17 masters (14 submittable), 1 Single, 19 child tables.

### Repository layout

```
construction_management_suite/
  hooks.py              app wiring
  setup.py              after_install / after_migrate / before_uninstall
  modules.txt           8 modules
  patches.txt           post_model_sync patches
  api/boq.py            the whitelisted API (25 endpoints)
  utils/                settings, billing, titles, accounting,
                        validations, permissions, helpers, notifications
  overrides/            project_dashboard
  public/js/cms.js      global desk JS, CMS.* helpers, CMS.calc
  fixtures/             number cards (the workspace is NOT a fixture — see below)
  demo/sample_project.py  a full mid-flight project
  tests/                parity + lifecycle harnesses
  <module>/doctype/...  controllers
  <module>/report/...   script reports
  <module>/print_format/...
```

### The one architectural rule

A construction document is the **parent**; the ERPNext document it raises is the **child**. Links run one way for cancellation: cancelling the parent cancels the child, cancelling the child leaves the parent alone. Getting this backwards once meant cancelling a Sales Invoice silently cancelled the certificate that produced it.

## Install, migrate, build

```bash
bench get-app construction_management_suite <repo-url>
bench --site <site> install-app construction_management_suite
```

`required_apps = ["frappe", "erpnext"]`. Always pass `--site` explicitly — `default_site` is not to be trusted.

### What each entry point does

| Hook | Does |
| --- | --- |
| `after_install` | Creates the 7 roles, the custom fields on ERPNext doctypes, and the 3 billing service items |
| `after_migrate` | Re-runs custom fields and billing items — idempotent, never overwrites a chosen setting |
| `before_uninstall` | Removes the custom fields |

`create_billing_items()` is wrapped in a bare `except` that logs rather than raises. A fresh site may have no Item Groups or UOMs yet, and a failure there must never abort an install.

### After a change

| Changed | Run |
| --- | --- |
| Python controllers, hooks, doctype JSON | `bench --site <site> migrate` |
| `public/js/cms.js` or any form script | `bench build --app construction_management_suite`, then tell the user to hard-refresh |
| A standard `.json` for a Print Format | Bump its `modified` in the same edit, then migrate |

That last row matters. `import_file` compares the `modified` inside the app's `.json` against the row in the database and **skips the import when they match**. Editing only the `html` deploys nothing: the file is right, migrate is clean, and the database keeps the old copy. Confirmed for Print Format; DocType JSON does not behave this way.

A stale asset bundle produces symptoms that look nothing like a build problem — including core JavaScript errors in `form.js` with no reference to this app. When something behaves impossibly, rebuild before debugging.

## hooks.py

### The rule that must not be broken

```python
doc_events = {
    "Purchase Order": {
        "validate": "construction_management_suite.project_costing"
                    ".purchase_controls.validate_purchase_order",
    },
}
```

That is the whole dict, and it must stay that way. **This app's own doctypes must never appear in `doc_events`.** Frappe already calls the controller's `on_submit` / `on_cancel`; a hook that re-calls it runs every side effect twice — duplicate invoices, duplicate stock entries. Only doctypes this app does *not* own belong here.

### The rest, annotated

| Hook | Value | Note |
| --- | --- | --- |
| `app_include_js` | `/assets/construction_management_suite/js/cms.js` | Served from the symlinked `public/`, so a rebuild is immediate |
| `app_include_css` | `css/cms.css` | |
| `fixtures` | Custom Field `cms_%`, Property Setters on 8 doctypes, the 7 roles | The workspace is deliberately absent |
| `scheduler_events` | daily: cost variance, retention eligibility, forecast recompute; weekly: subcontractor aging | All four resolve |
| `jinja.methods` | `format_currency_arabic`, `number_to_words_arabic`, `get_project_summary` | For print formats |
| `jinja.filters` | `arabic_number` | |
| `regional_overrides` | Saudi Arabia, United Arab Emirates VAT | `localization/` also holds gcc, india, pakistan, europe, africa stubs |
| `permission_query_conditions` | BOQ, Cost Estimation, IPC, Daily Site Report → `get_company_filter` | Row-level security by company |
| `has_permission` | BOQ, IPC | |
| `override_doctype_dashboards` | `Project` → `overrides.project_dashboard.get_dashboard_data` | The construction documents in the Project's Connections tab |
| `notification_config` | `utils.notifications.get_notification_config` | |
| `after_install` / `after_migrate` / `before_uninstall` | `setup.py` | |

Every hook target above resolves through `frappe.get_attr` — verified, not assumed.

`dashboards` is **not** a Frappe hook. Nothing reads it. `override_doctype_dashboards` is the real one, and the difference cost a debugging session.

## The document map

### What each submittable document raises

| Doctype | `on_submit` raises | `on_cancel` undoes |
| --- | --- | --- |
| BOQ | Links the Project's BOQ reference | Unlinks it |
| Variation Order | Moves `Project.cms_contract_value` by the net | Moves it back |
| Cost Estimation | A **Project Budget** from its lines | — |
| Interim Payment Certificate | A **Sales Invoice** (single line, net payable) + pushes certified qty to the BOQ | Cancels the invoice, re-pushes qty |
| Retention Release | A **Sales Invoice** | Cancels it |
| Subcontract Agreement | A **Purchase Order** | — |
| Subcontractor Payment Certificate | A **Purchase Invoice** + refreshes the agreement totals | Cancels it, refreshes again |
| Material Consumption Entry | A **Stock Entry** (Material Issue) | Cancels it |
| Site Transfer | A **Stock Entry** (Material Transfer) | Cancels it |
| Site Material Request | A **Material Request** | Cancels it |
| Daily Site Report | Updates `Project.percent_complete` | — |
| Project Budget | — | — |
| Material Forecast | — | — |
| Subcontractor Work Order | — (progress via `on_update_after_submit`) | — |

### Where status is set, and why

Status is assigned in **`before_submit` / `before_cancel`**, never in `on_submit` / `on_cancel`.

`on_submit` and `on_cancel` run **after** the row has been written. Setting `self.status` there is discarded silently — the document submits, the side effects fire, and the status field keeps its old value. Every controller in this app follows the same shape:

```python
def before_submit(self):
    self.status = "Submitted"      # persisted with the submit

def on_submit(self):
    self._create_sales_invoice()   # side effects only
```

### `validate` ordering

`validate()` runs **before** `_validate_mandatory()`. A controller may therefore fill a mandatory field in `validate` and it will pass. That is how `set_contract_value`, `set_previous_position` and `set_previous_claimed` work on the IPC.

Derived positions — previous cumulative amount, previously-claimed quantity per line, retention held — are **recomputed on every save of a draft**, never trusted from what was typed. Cancelling an earlier certificate moves them, and a stale figure double-bills a client.

### Progress on a submitted document

The Subcontractor Work Order records progress after submission. Three things are all required:

1. `allow_on_submit` on the **child field** (`completed_qty`)
2. `allow_on_submit` on the **parent Table field** (`items`) — without this the child flag does nothing
3. `on_update_after_submit` that recomputes **and** `db_set`s the derived values

`update_after_submit` writes only the allow-on-submit fields the client sent. Anything derived from them — `completed_amount`, `completion_percent`, the document totals, the status — must be written explicitly:

```python
def on_update_after_submit(self):
    self.calculate_totals()
    self.set_status()
    for row in self.items:
        row.db_set("completed_amount", flt(row.completed_amount), update_modified=False)
        row.db_set("completion_percent", flt(row.completion_percent), update_modified=False)
    for field in ("total_contract_value", "total_completed_value",
                  "completion_percent", "status"):
        self.db_set(field, self.get(field), update_modified=False)
```

## Settings and the Ignore / Warn / Stop pattern

`utils/settings.py` is four functions and they are the only way this app should read policy:

```python
cms_settings()                      # the cached Single
cms_setting(fieldname, default)     # one value
action_for(fieldname, default="Warn")
enforce(action, message, title)     # Ignore | Warn (msgprint) | Stop (throw)
enforce_setting(fieldname, message, title, default)
```

The three-level vocabulary is ERPNext Budget's, deliberately — users already know it.

A check is written once and reads its severity at run time:

```python
action = action_for("over_certification_action", "Stop")
if action == "Ignore":
    continue
enforce(action, _("Row {0}: ...").format(item.idx), title=_("Over-certification"))
```

### Every enforcement point

| Setting | Enforced in |
| --- | --- |
| `unapproved_analysis_action` | `boq.py :: validate_analyses_approved` (before_submit) |
| `below_cost_action` | `boq.py :: warn_priced_below_cost` (validate) |
| `below_minimum_margin_action` + `minimum_margin_percent` | `boq.py :: validate_minimum_margin` (before_submit) |
| `po_over_budget_action` | `project_costing/purchase_controls.py` (PO validate hook) |
| `purchase_rate_action` + `purchase_rate_tolerance_percent` | same file |
| `subcontract_above_cost_action` | `subcontract_agreement.py :: check_against_boq` |
| `advance_recovery_action` + `advance_recovery_threshold_percent` | `utils/billing.py :: check_advance_recovery`, called from both certificates |
| `over_certification_action` | `interim_payment_certificate.py :: validate_over_certification` |
| `consumption_over_takeoff_action` + `consumption_tolerance_percent` | `material_consumption_entry.py :: check_against_take_off` |
| `work_item_type_action` | `utils/validations.py :: validate_work_item` — BOQ, Cost Estimation, Variation Order, Rate Analysis, and every work row on a material document |
| `material_resource_action` | `validate_material_resources` (Rate Analysis, on approval) and `validate_material_item` (every material row) |
| `missing_rate_analysis_action` | `validate_rate_analysis_present` — Cost Estimation |
| `no_estimate_action` | `require_estimate` — consumption and forecast |
| `uom_mismatch_action` | `validate_uom_convertible` — Rate Analysis resources, forecast, consumption, site request, transfer |

`scope_rate_analysis_by_company` is a Check rather than an action: on, an item's analysis is looked up inside the document's company, the pickers filter to it and a line pointing at another company's analysis is refused.

Defaults that are values rather than actions are read the same way: `default_rate_source`, `default_selling_price_list`, `default_contingency_percent`, `default_retention_percent`, `default_subcontract_retention_percent`, the three billing items, `consumption_expense_account`.

**Tax templates are not among them.** They were, and it was wrong: a template carries accounts and accounts belong to a company, so one site-wide template put one company's VAT on another company's agreement. `billing.default_tax_template(doc)` reads ERPNext's own per-company default (`is_default` on the template) through `get_default_taxes_and_charges`, and `validate_tax_template_company` refuses a template from another company on all four doctypes that carry tax.

### Two traps in a Single

**Cache invalidation.** `ConstructionSettings.on_update` must clear the *document* cache, not the meta:

```python
def on_update(self):
    frappe.clear_document_cache(self.doctype, self.name)
```

`frappe.clear_cache(doctype=...)` clears the meta and leaves the cached document, so a changed setting appears to have no effect.

**Defaults never apply retroactively.** A docfield `default` runs only when a document is created. Adding a field to an existing Single leaves it empty, so a new Select needs a patch — see `patches/v1_1/apply_new_setting_defaults.py`, which uses `frappe.get_meta(...).get_field()`; `frappe.db.has_column` throws on a Single because the values live in `tabSingles`.

**A Check is worse than that, and it is silent.** A Single is loaded out of `tabSingles` and a field with no row there comes back cast to `0` — so a checkbox whose docfield default is `1` reads as *off* to every line of server code while the form shows it ticked. A Select survives this because blank means "use the module default" and the code says what that default is; a Check has no blank state. `setup.seed_check_defaults()` writes the docfield default of any unstored Check on install and on migrate, once, and never again — a site that unticks a box stores a `0`, and a `0` is a row. Add a Check to Construction Settings and it is covered; do not rely on the `default` alone.

Also: a hardcoded docfield `default` always beats a value read from settings. A retention default of 4 in the settings showed as 10 on three forms because the docfields carried `"default": "10"`.

## Shared utilities

Eight modules under `utils/`. Anything used by more than one controller lives here.

### `billing.py` — invoices, taxes, recovery

The busiest module. Both client-side and supplier-side certificates route through it, which is what keeps them arithmetically identical.

| Function | Does |
| --- | --- |
| `billing_item(setting_name)` | The service item for a generated invoice line, from settings or the shipped default |
| `create_service_items()` | Idempotent creation of `SRV-PROGRESS-BILLING`, `SRV-RETENTION-RELEASE`, `SRV-SUBCONTRACT` |
| `add_line(doc, item, amount, description)` | The single invoice line |
| `load_tax_template(doc)` / `apply_taxes` | Pull a tax template's rows onto a document |
| `calculate_taxes(doc, base_amount)` | The tax engine — Actual, On Net Total, On Previous Row Amount, On Previous Row Total |
| `carry_taxes(source, target)` | Copy tax rows to the generated invoice |
| `refuse_empty(doc)` | Never submit an invoice with no lines |
| `orderable_qty(qty, uom)` | Rounds **up** for a whole-number UOM |
| `money(doc, value)` | Formatted in the document's currency |
| `check_advance_recovery(...)` | The unrecovered-advance blocker, shared by both certificates |

`orderable_qty` exists because ERPNext refuses a fractional quantity on a UOM flagged `must_be_whole_number`, and rounding *down* a take-off leaves the job short.

### `titles.py` — auto-generated titles

```python
set_auto_title(doc, fieldname, parts)   # skips a title the user typed
month_of(date)
project_label(project)
```

Every similar document uses the same call, so titles read the same everywhere: `IPC #3 · Al Khuwair Office Block · Aug 2026`. A title the user typed is never overwritten.

### `accounting.py` — cost centres and the consumption account

```python
def get_cost_center(project=None, company=None):
    if project:
        cost_center = frappe.db.get_value("Project", project, "cost_center")
        if cost_center:
            return cost_center
    if company:
        return frappe.get_cached_value("Company", company, "cost_center")
    return None
```

Nothing is created. An earlier version auto-created a cost centre per project; creating accounts in someone's chart of accounts uninvited is not this app's business.

### The rest

| Module | Holds |
| --- | --- |
| `settings.py` | `cms_setting`, `action_for`, `enforce` |
| `validations.py` | `validate_project_company` and friends |
| `permissions.py` | `get_company_filter`, `has_permission` for the row-level hooks |
| `helpers.py` | The Jinja methods and filters for print formats |
| `notifications.py` | `get_notification_config` |

## The API surface

`api/boq.py` holds 25 whitelisted endpoints — despite the name, it is the module's whole API. Another 12 whitelisted methods sit on controllers.

### Pricing and rates

| Endpoint | Returns |
| --- | --- |
| `get_boq_line_rates(item_code, rate_source, price_list)` | One call deciding both the cost breakdown and the selling rate |
| `get_selling_rate(item_code, price_list)` | The price-list rate |
| `get_item_rate(item_code, company, basis, price_list)` | `basis=None` keeps the existing preference chain, so every old caller is unaffected |
| `get_item_valuation_rate(item_code, warehouse)` | |
| `get_rate_analysis_for_item` / `get_rate_analysis_rates` | |
| `price_boq_from_library(boq, overwrite, source, price_list)` | Bulk re-price |
| `check_rate_drift(boq)` | Frozen build-up against the library today |
| `get_rate_build_up(doctype, docname, idx)` | The snapshot a line was priced on |
| `apply_rate_analysis_to_boq(rate_analysis, boq, item_code)` | |

### Line pickers

`get_boq_lines_for_ipc`, `get_boq_lines_for_variation`, `get_boq_lines_for_subcontract`, `get_agreement_lines`, `get_completed_work`, `get_items_from_forecast`.

`get_boq_lines_for_subcontract` returns the **cost** rate, not the selling rate — what you agree to pay a trade is measured against what the work was costed at.

### Positions

`get_boq_summary`, `get_project_cost_dashboard`, `get_consumption_position`, `get_retention_summary`, `get_previous_ipc_position`, `get_site_progress_timeline`.

### Document makers

`make_cost_estimation`, `make_interim_payment_certificate`, `make_payment_certificate`, `create_material_request_from_forecast`.

Each uses `get_mapped_doc` with `_own_naming_series(target)`. **`get_mapped_doc` copies same-named fields, `naming_series` included** — which is how a Cost Estimation once came out named `BOQ-2026-0009`.

### Whitelisted controller methods

| Doctype | Methods |
| --- | --- |
| BOQ | `create_project`, `create_revision`, `import_from_template` |
| Rate Analysis | `make_new_version`, `update_cost` |
| Variation Order / Subcontract Agreement | `add_boq_lines` |
| Subcontract Agreement | `refresh_payment_summary` |
| Subcontractor Work Order | `get_scope_from_agreement` |
| Subcontractor Payment Certificate | `get_completed_from_work_orders` |
| Interim Payment Certificate / Material Forecast | `get_items_from_boq` |
| Material Consumption Entry | `get_items_from_forecast` |
| Project Budget | `refresh_actuals` |

One thing to know when calling these from the client: **`frm.call('method')` sends `frm.doc`** — the in-memory document, including unsaved edits. A method that nets against "what is already there" must decide whether it means the database or the form, and say so.

## The client layer

`public/js/cms.js` loads on every desk page via `app_include_js`. It carries the shared helpers and, importantly, `CMS.calc`.

### Why the arithmetic exists twice

Figures on screen are computed in the browser so the user sees them live, and again on the server when the document saves. If the two drift, a user commits to a number the system then changes underneath them.

`CMS.calc` is keyed by parent doctype, one function per document, mirroring the controller's `calculate_*` methods exactly:

```javascript
CMS.calc["Subcontractor Work Order"] = function (doc) {
    let contract = 0, done = 0;
    (doc.items || []).forEach(r => {
        r.contract_amount  = flt(r.contract_qty) * flt(r.contract_rate);
        r.completed_amount = flt(r.completed_qty) * flt(r.contract_rate);
        ...
    });
    doc.total_contract_value = contract;
};

CMS.recalc = function (frm) {
    const fn = CMS.calc[frm.doc.doctype];
    if (!fn) return;
    fn(frm.doc);
    frm.refresh_fields();
};
```

`CMS.liveRows(childDoctype, fields)` wires every input of a child table to `CMS.recalc`, so the parent updates on each keystroke.

This duplication is **load-bearing and tested** — see the parity harness. It has repeatedly caught real disagreements: the IPC tax base, the Retention Release balance, the subcontractor certificate's certified amount.

### The other helpers

| Helper | Does |
| --- | --- |
| `CMS.uomQuery(frm, table, itemfield)` | Restricts a row's UOM to the ones on its Item, honouring Stock Settings |
| `CMS.fetchItemRate(frm, cdt, cdn, opts)` | Fills a blank rate and says where the number came from |
| `CMS.defaultTaxTemplate(frm, setting)` | Loads the module's tax template onto a new document |
| `CMS.loadTaxTemplate(frm)` | Pulls a chosen template's rows |
| `CMS.filterProjects`, `CMS.filterByProject`, `CMS.clearForeignProject` | Company- and project-scoped link queries |
| `CMS.linkButton(frm, label, doctype, name)` | A button to a generated ERPNext document |
| `CMS.showRateBuildUp(frm)` | The frozen build-up beside the library's figures |
| `CMS.rateAnalysisQuery(frm, table)` | Scopes the picker to the row's item and approved analyses |
| `CMS.scopeItemPickers(frm)` | Scopes every Item link on the form and in its grids to the form's company |

### Item pickers follow the form's company

`Item.company` is a Custom Field this app does **not** own — another app on the site adds it — so `CMS.scopeItemPickers` first checks `frappe.meta.has_field("Item", "company")` (once per session, after `frappe.model.with_doctype("Item")`) and does nothing where the field is absent.

It hangs off `$(document).on("form-refresh")`, the one global form hook, rather than a handler per doctype, and finds the fields from each form's own meta — parent Link fields with `options: "Item"`, plus the same in every child table. On `misk` that is 14 fields across 13 forms; `BOQ Template` and `Construction Settings` are skipped because they have no company to scope to.

The filter is `[["company", "in", [company, ""]]]`. The blank is what lets shared items through: `frappe.get_all` compares `coalesce(company, '')`, so `''` matches a NULL as well as an empty string — which matters, because on `misk` 118 of 582 items have `company IS NULL` and none have `''`.


### Form conventions

Field `description` stays under about 40 characters, or is omitted when the label already says it. No `frm.dashboard.add_comment()` paragraphs — a state worth flagging gets `frm.page.set_indicator()`, a button, or the Connections tab. Long help text pushes the actual fields off the screen and stops being read.

## Reports, print formats and the workspace

### Reports

Four Script Reports are installed:

| Report | Module | `ref_doctype` |
| --- | --- | --- |
| BOQ Summary | BOQ Management | BOQ |
| Resource Take-off | BOQ Management | Cost Estimation |
| Material Position | Material Planning | Material Forecast |
| Project Cost Variance | Project Costing | Project Budget |

Three report folders contain only an `__init__.py` and install nothing: `progress_billing/report/billing_summary`, `project_costing/report/cash_flow_projection`, `project_costing/report/profitability_analysis`. They are placeholders, not features.

**A report needs its `.js`.** `BOQ Summary` and `Project Cost Variance` have no `.js`, so the filters their Python reads (`filters.get("project")`) are dead — nothing ever supplies them. `Resource Take-off` and `Material Position` ship theirs.

### Print formats

Five standard formats: BOQ, Variation Order, Interim Payment Certificate, Subcontract Agreement, Subcontractor Payment Certificate. The BOQ format deliberately prints the selling side only.

Remember the `modified` bump when editing one.

### The workspace

`Construction Management Suite`, module BOQ Management, shipped as a **standard workspace** under `boq_management/workspace/construction_management_suite/`. Frappe syncs it from there on every migrate.

It used to be exported as `fixtures/cms_workspace.json` as well. That put two files in charge of one page, and `import_fixtures` reads **every** `.json` in `fixtures/` regardless of what the `fixtures` hook lists — so which one won depended on ordering. The fixture is gone and the hook entry with it. Edit the module JSON and nothing else.

Three structures have to agree, and they are stored separately:

1. The **`content`** field — a JSON **string** of blocks, each naming the card, chart or shortcut it draws
2. The **`number_cards`** child table
3. The **Number Card documents** themselves

Renaming a Number Card updates the document and the child table but **not the string**, and the page then silently draws nothing. That is exactly what happened when `CMS Contract Value` became `Contract Value`.

Equally: each **Card Break** carries a `link_count` that must match the links beneath it. A mismatch truncates the card without an error.

Eight number cards: Contract Value, Approved Variations, Billed to Date, Retention Held, Actual Cost, Subcontracted, Certificates Awaiting, Safety Incidents.

## Custom fields and the links between the two sides

Thirteen custom fields, all prefixed `cms_` — which is what the fixture filter matches, so the prefix is not decorative.

| ERPNext doctype | Field | Type | Purpose |
| --- | --- | --- | --- |
| Project | `cms_project_type` | Select | Building, Civil, MEP, Infrastructure, Fit-Out, Roads, Oil & Gas, Other |
| Project | `cms_contract_value` | Currency | Moved by approved Variation Orders |
| Project | `cms_client_po` | Data | Client PO / contract number |
| Project | `cms_retention_percent` | Percent | Default for the project's certificates |
| Project | `cms_advance_amount` | Currency | What the advance-recovery check measures against |
| Sales Invoice | `cms_ipc_ref` | Link | Back to the certificate |
| Sales Invoice | `cms_retention_release_ref` | Link | Back to the release |
| Purchase Order | `cms_subcontract_ref` | Link | Back to the agreement |
| Purchase Invoice | `cms_subcontract_certificate_ref` | Link | Back to the certificate |
| Stock Entry | `cms_consumption_ref` | Link | Back to the consumption entry |
| Stock Entry | `cms_site_ref` | Link | Back to the site transfer |
| Material Request | `cms_forecast_ref` | Link | Back to the forecast |
| Material Request | `cms_site_request_ref` | Link | Back to the site request |

### The direction of the link matters

Every one of these lives on the **ERPNext** document and points **back** at the construction document. The construction document also stores the generated document's name, but the pair is not symmetric by accident.

Frappe builds its cancellation cascade from link fields. A link **from** the certificate **to** the invoice, combined with one the other way, made cancelling the invoice cancel the certificate. Seven reverse links were removed to fix it. The convention now:

- The ERPNext document carries the `cms_*` link back to its parent
- The parent stores the child's name in a plain reference field it manages itself
- The child appears in the parent's Connections tab, not as a hard dependency

### Amendability

Every submittable doctype needs `amended_from`, `status` needs `no_copy`, and any status Select needs a `Cancelled` option. Twelve of fourteen doctypes were missing `amended_from` at one point, which made a cancelled document impossible to correct.

## Testing

Three harnesses under `tests/` and `demo/`. None of them is a Frappe unit test; all three exist because unit tests were not catching the failures that actually happened.

### Parity — `tests/client.js` + `tests/server.py`

Runs the browser arithmetic and the server arithmetic on identical input and compares every computed field.

```bash
cd construction_management_suite/tests
node client.js ../public/js/cms.js > /tmp/client.json
cd /home/ramees/frappe-bench/sites
../env/bin/python ../apps/construction_management_suite/construction_management_suite/tests/server.py > /tmp/server.json
# diff the two, tolerance 0.0005
```

`client.js` stubs just enough Frappe to `eval` the real `cms.js`. `cases.json` deliberately holds the awkward inputs: zero contract quantity (the division guard), a fully-completed line, a subcontract resource, over-ordered material, overtime.

Current state: **299 values across 13 doctypes, 0 mismatched.** Server-owned fields such as `already_ordered_qty` are excluded — the browser can only ever use what the server last put there.

### Lifecycle — `tests/lifecycle.py`

Cancels, amends and re-submits every submitted document on the demo project. It exists because a module import cannot catch a name used only at run time — it immediately found a `report_date > nowdate()` comparing a date to a string.

A `frappe.LinkExistsError` is recorded as `GUARD`, not a failure: something depending on the document is exactly the protection working.

Current state: **12 doctypes, 0 failures.**

### Demo data — `demo/sample_project.py`

Builds a complete mid-flight project (`Al Khuwair Office Block`): a priced BOQ, a cost estimation and budget, three payment certificates with their invoices, a variation, subcontracts with work orders and certificates, material forecasts, consumption, site reports. Idempotent by project name.

Use it to exercise reports and the workspace against realistic data rather than an empty site.

### A caution when writing test scripts

`frappe.log_error` **commits**. A rollback-wrapped test that triggers an error log will leave that log behind, and anything written after it. Clean up explicitly rather than relying on the rollback.

## Patches

All five current patches are `post_model_sync` — they read and write columns the release adds, so they must run after the schema sync.

| Patch | Does |
| --- | --- |
| `link_ipc_lines_and_derive_margin` | Links existing IPC lines to their BOQ rows; recomputes derived margin |
| `tender_sum_and_item_numbers` | Backfills `item_no` and reconciles the grand total |
| `link_subcontract_chain` | Connects agreements, work orders and certificates already in the field |
| `backfill_work_order_status` | Sets the status on work orders that predate the field |
| `apply_new_setting_defaults` | Writes defaults into Construction Settings for fields added after install |

That last one is the pattern to copy whenever a setting is added:

```python
field = frappe.get_meta("Construction Settings").get_field(fieldname)
```

not `frappe.db.has_column`, which throws on a Single because the values live in `tabSingles`.

A patch that writes to a Single must also clear the document cache afterwards, for the same reason `on_update` does.

## Extension recipes

### Add a setting

1. Add the field to `construction_settings.json`, in an existing section or a new one.
2. Read it through `cms_setting(fieldname, default)` — never `frappe.db.get_single_value` scattered through controllers.
3. Add a patch under `patches/v1_1/` to write the default onto existing sites, using `frappe.get_meta(...).get_field()`.
4. If a form should pre-fill from it, do it in `validate` or the form's `onload`, and make sure no docfield carries a hardcoded `default` that would win.

### Add a blocker

1. Add a Select field with options `Ignore\nWarn\nStop` and a sensible default.
2. Write the check as a controller method called from `validate` or `before_submit`.
3. Read the severity with `action_for("my_action", "Warn")` and raise it with `enforce(...)`.
4. Make the message say what to do instead, with the numbers in it. Every existing message names the row, the figures and the remedy — "raise a Variation Order, or reduce this period's quantity to 68".
5. Add the row to the user guide's blocker table.

### Add a document that raises an ERPNext document

1. Give it `amended_from`, a `status` field with `no_copy` and a `Cancelled` option.
2. Set status in `before_submit` / `before_cancel`, side effects in `on_submit` / `on_cancel`.
3. Add a `cms_*` custom field on the ERPNext doctype pointing **back** at yours; store the generated name on your side in a plain field.
4. Do **not** add your doctype to `doc_events`.
5. If the figures are shown live, add a `CMS.calc` entry and a case to `tests/cases.json`.

### Add a report

1. Ship the `.js` with the filters. Without it, `filters.get(...)` in the Python is dead code.
2. `ref_doctype` decides where it appears in the desk.
3. Add it to the workspace JSON under `boq_management/workspace/` — there is only one — and recompute the Card Break's `link_count`.
4. **Bump `modified` in that JSON.** `import_file_by_path` compares the file's
   `modified` against the record's and skips the import when they match, so an
   edited workspace with an untouched timestamp is a silent no-op on migrate.
   This is not theoretical: it is what made the first attempt at this change
   appear to do nothing.

This applies to **every** standard record this app ships as a JSON file — the
workspace, the five print formats, anything added later. Edit the file, bump
`modified`, migrate, and check the record in the database rather than the file
on disk. Both times it has been forgotten, the change looked applied and was
not.
4. Bump `modified` on any standard JSON you touch.

### Change a calculation

Change it in the controller **and** in `CMS.calc`, then run the parity harness. A change in one place only is the failure mode this whole harness exists to catch.

## Frappe behaviours this app was bitten by

Each of these cost real debugging time. They are collected here so the next person spends it on something else.

### Lifecycle and documents

- `validate()` runs **before** `_validate_mandatory()`. A controller can fill a mandatory field in `validate`.
- `on_submit` / `on_cancel` run **after** the row is written. `self.status = X` there is discarded — use `before_submit` / `before_cancel`.
- `Document.insert()` calls `_set_defaults()` before `validate` **and** before `before_insert`.
- `allow_on_submit` must be on **both** the child field and the parent Table field.
- `update_after_submit` writes only the allow-on-submit fields the client sent. Derived values need explicit `db_set`.
- `get_mapped_doc` copies same-named fields, `naming_series` included.
- `frappe.copy_doc(doc)` defaults to `ignore_no_copy=True`; the desk's own Amend uses `False`.
- `frappe.log_error` **commits** — test rollbacks do not undo everything.

### Fields and defaults

- Frappe fills a blank **Select** with `options.split("\n")[0]` during insert (`create_new.get_static_default_value`). A blank first option is sometimes the only way a server-side default can win.
- `frappe.defaults` applies to **Link** fields only, not Select.
- A hardcoded docfield `default` always beats a value read from settings.
- A field's `default` runs only at document creation. Adding a field to an existing **Single** leaves it empty, and for a Check `0` is indistinguishable from unset.
- `frappe.db.has_column` **throws on a Single** — the values live in `tabSingles`.
- Grid column budget is 11 units including the row index.

### Caching and deployment

- `frappe.clear_document_cache(doctype, name)` clears the cached document; `frappe.clear_cache(doctype=...)` clears the **meta** and leaves the document. For a Single, the first is what you want.
- `import_file` skips a standard `.json` whose `modified` matches the DB row. Confirmed for Print Format; DocType JSON does not behave this way.
- A Workspace's `content` is a JSON **string**; number cards are named inside it and renaming the card document does not touch it.
- A Card Break's `link_count` must match its links.

### Names and modules

- **Module names are global across the bench.** `Setup` collides with ERPNext's. Check with `frappe.db.get_value("Module Def", name, "app_name")` before adding one.
- `frappe.utils.is_html()` checks for **tags**, not entities. A tagless string containing `&nbsp;` renders the entity literally through `.text()`.

### ERPNext specifics

- A Sales Invoice with a negative rate will **insert** but not **submit** unless *Allow Negative rates for Items* is on. Test with `submit()`, not `insert()`.
- A fractional quantity is refused on a UOM flagged `must_be_whole_number`.
- Purchase Invoice links to a Purchase Order through `items[].purchase_order` + `po_detail`, and the item must match.
- `non_standard_fieldnames` in a dashboard resolves each doctype through its own link field.

### Working in this bench

- Always `git -C <app path>` — the bench root is a repo of repos and `git add -A` from there stages a hundred thousand files.
- Always pass `--site` explicitly. `default_site` is volatile and often blank.
- A shallow clone blocks a push (`remote unpack failed: index-pack failed`). Fix with `git fetch --unshallow <remote>`.

## Known gaps

Honest list, current as of 24 September 2026.

### Material workflow

The three gaps listed here before — no picker filling the work reference on a
consumption entry, no take-off picker on a Site Material Request, and an unread
`qty_wasted` — are closed. Work is tracked by `cms_work_item` (the work Item
itself, not a child-row name), both documents have a take-off picker, and waste
now lives in the quantity rather than in a field nothing read.

| Gap | Effect |
| --- | --- |
| No returns or wastage path | Material issued and later returned has no document |
| `Cost Code` is not a tree | `parent_cost_code` exists, but the doctype is not `is_tree`, there is no nested set, and nothing rolls a child's spend to its parent |

### Reports

Three report folders are empty placeholders: `billing_summary`, `cash_flow_projection`, `profitability_analysis`. `Project Cash Flow Item` exists with nothing populating it.

### Buying side

The Purchase Invoice a subcontractor certificate raises is **not** linked to the Purchase Order the agreement raised. ERPNext links the two through `items[].purchase_order` + `po_detail` with a matching item, which would require the invoice to mirror the order's item lines — and that conflicts with the single-line net-payable invoice the module deliberately produces. Left open on purpose; it needs a decision, not a patch.

### Deliberately not done

- **No cascade from Rate Analysis to BOQ or Cost Estimation.** ERPNext's BOM cascades `update_cost` to parent BOMs. This must not, because a BOQ line holds a frozen `rate_build_up` precisely so a signed bill cannot move. `check_rate_drift` surfaces the difference instead.
- **No per-company account table.** The single-line invoice made it unnecessary; accounts and tax templates are site-wide settings.
- **No automatic cost centre creation.** The project's own, else the company default, else nothing.
- **Back-to-back subcontract terms are not enforced.** The defaults are set to encourage them (10% subcontract retention against 5% client) but the relationship is a commercial judgement, not a rule.

### Localization

`localization/` holds `ksa`, `uae`, `gcc`, `india`, `pakistan`, `europe`, `africa`. Only KSA and UAE are wired into `regional_overrides`, and both provide VAT settings only.
