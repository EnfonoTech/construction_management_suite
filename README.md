# Construction Management Suite

A production-ready, modular **Construction Management Suite** built as a Frappe/ERPNext application. Designed for construction companies, contractors, subcontractors, MEP firms, civil works companies, infrastructure companies, and interior fit-out businesses operating globally.

[![Frappe](https://img.shields.io/badge/Built%20on-Frappe%20v15-blue)](https://frappeframework.com)
[![ERPNext](https://img.shields.io/badge/Requires-ERPNext%20v15-green)](https://erpnext.com)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

---

## Overview

Construction Management Suite (CMS) extends ERPNext with 7 specialized modules covering the full construction project lifecycle from Bill of Quantities and cost estimation through site operations, progress billing, subcontractor management, and material planning.

---

## Modules

| Module | Key Doctypes | Purpose |
|---|---|---|
| **BOQ Management** | BOQ, BOQ Template | Bill of Quantities creation, revisions, templates, rate linkage |
| **Estimation** | Rate Analysis, Cost Estimation | Resource-based costing, tender estimation, rate analysis |
| **Project Costing** | Project Budget, Cost Code | Budget monitoring, variance tracking |
| **Site Management** | Daily Site Report, Site Material Request | Field operations, attendance, progress tracking |
| **Progress Billing** | Interim Payment Certificate, Retention Release | IPC billing, retention handling |
| **Subcontractor Management** | Subcontract Agreement, Work Order, Payment Certificate | Subcontract lifecycle, work orders, payment certs |
| **Material Planning** | Material Forecast, Site Transfer, Consumption Entry | Procurement planning, forecasting, site-to-site transfers |

---

---

## Scope and known limits

Every DocType in this app is installed and working. Ten DocTypes that previously
shipped as empty Python stubs with no JSON — and so were never installed — have been
removed rather than left to imply features that do not exist:

`BOQ Revision`, `BOQ Revision Item`, `Running Bill`, `Running Bill Item`,
`Resource Template`, `Resource Template Item`, `Cost Variance Ledger`,
`WIP Entry`, `WIP Entry Item`, `Site Attendance`.

Four of those were redundant in any case: BOQ revisions are handled by
`BOQ.revision_no` / `previous_boq` / **Create Revision**, and a "running bill" is
the Interim Payment Certificate under another name.

Not built, and worth knowing before you scope a project around this app:

- **No Variation Order DocType.** Contract scope changes have no home; they have to
  be handled as a BOQ revision, which loses the approval trail. This is the largest
  functional gap.
- **No print formats.** A BOQ and an IPC are documents you print, sign and issue to a
  client. Both currently print with the Frappe standard format only.
- **No WIP journals.** Work-in-progress is not posted at month end.
- **No test suite.**

`Cost Code` is a working WBS tree, but its actuals only populate for Project Budget
rows that name a cost code whose **Debit Account** is set — spend is matched by GL
account, so rows without one keep whatever was entered by hand.

## Requirements

- **Frappe** v15
- **ERPNext** v15
- Python 3.10+
- MariaDB 10.6+

---

## Installation

### 1. Get the app

```bash
cd /path/to/frappe-bench

# Option A — from GitHub
bench get-app https://github.com/Zaryab03/construction_management_suite

# Option B — if already cloned locally
bench get-app construction_management_suite /path/to/construction_management_suite
```

### 2. Install on a site

```bash
bench --site <your-site> install-app construction_management_suite
```

### 3. Migrate

```bash
bench --site <your-site> migrate
```

### 4. (Optional) Build assets

```bash
bench build --app construction_management_suite
```

### 5. Restart

```bash
bench restart
```

---

## Quick Start

After installation, navigate to **Construction Management Suite** in the ERPNext sidebar.

### Recommended setup order

1. **Setup → Cost Codes** — define your WBS / cost code structure
2. **BOQ Management → BOQ Template** — create reusable BOQ templates per project type
3. **Estimation → Rate Analysis** — build a rate library (labour, material, equipment)
4. **Start a project** (ERPNext Projects module) — the CMS custom fields are automatically available
5. **BOQ Management → BOQ** — create the project BOQ, import from template, link rate analyses
6. **Estimation → Cost Estimation** — build the tender/budget estimation
7. **Project Costing → Project Budget** — auto-created from approved Cost Estimation
8. **Site Management → Daily Site Report** — engineers submit daily from mobile/desktop
9. **Progress Billing → IPC** — create Interim Payment Certificates, auto-generates Sales Invoice
10. **Subcontractor Management → Subcontract Agreement** — auto-creates linked Purchase Order

---

## Roles

| Role | Access |
|---|---|
| `CMS Admin` | Full access to all CMS modules |
| `CMS Project Manager` | Read/write on all modules, submit workflows |
| `CMS Quantity Surveyor` | BOQ, Estimation, Project Costing |
| `CMS Site Engineer` | Daily Site Report, Site Material Request, Material Consumption |
| `CMS Billing Officer` | IPC, Retention Release, Subcontractor Payment Certs |
| `CMS Subcontractor` | Read own Subcontractor Payment Certificates (portal) |
| `CMS Viewer` | Read-only across all modules |

---

## ERPNext Integration Points

The app extends ERPNext without modifying core:

- **Project** — adds `cms_project_type`, `cms_contract_value`, `cms_client_po`, `cms_retention_percent`
- **Purchase Order** — adds `cms_subcontract_ref` (linked to Subcontract Agreement)
- **Sales Invoice** — adds `cms_ipc_ref` (linked to IPC)
- **Stock Entry** — adds `cms_site_ref` (linked to Site Transfer)
- **GL Entry / Budget** — Project Budget reads actual costs from ERPNext GL entries
- **Timesheet** — Daily Site Report can optionally generate timesheets

---

## Localization Support

Optional regional localization modules are included:

| Region | File | Rules |
|---|---|---|
| Saudi Arabia (KSA) | `localization/ksa/overrides.py` | 15% VAT, ZATCA e-invoicing, Arabic print formats |
| UAE | `localization/uae/overrides.py` | 5% VAT, FTA compliance |
| Pakistan | `localization/pakistan/overrides.py` | 17% GST, 6% WHT on subcontractors |
| India | `localization/india/overrides.py` | 18% GST (CGST/SGST/IGST split), 2% TDS 194C |
| GCC / Africa / Europe | *(stubs ready)* | Extend per region |

---

## API Endpoints

All endpoints are whitelisted Frappe methods callable via REST or JS:

```python
# Get BOQ summary for a project
construction_management_suite.api.boq.get_boq_summary(project)

# Project cost dashboard KPIs
construction_management_suite.api.boq.get_project_cost_dashboard(project)

# Site progress timeline
construction_management_suite.api.boq.get_site_progress_timeline(project, limit=30)

# Convert Material Forecast → ERPNext Material Request
construction_management_suite.api.boq.create_material_request_from_forecast(forecast_name)

# Retention summary (held vs released)
construction_management_suite.api.boq.get_retention_summary(project)

# Push Rate Analysis rates to BOQ items
construction_management_suite.api.boq.apply_rate_analysis_to_boq(rate_analysis, boq, item_code)
```

---

## Scheduled Jobs

| Frequency | Job | Description |
|---|---|---|
| Daily | `project_costing.utils.calculate_daily_variance` | Recalculate budget variance for all active projects |
| Daily | `progress_billing.utils.check_retention_release` | Notify when DLP periods expire |
| Daily | `material_planning.utils.recompute_forecasts` | Update ordered quantities on Material Forecasts |
| Weekly | `project_costing.utils.refresh_cash_flow_projections` | Refresh cash flow projections |
| Weekly | `subcontractor_management.utils.generate_aging_report` | Log subcontractor payment aging |
| Monthly | `project_costing.utils.create_monthly_wip_entries` | Create WIP journal entries |

---

## Folder Structure

```
construction_management_suite/
├── construction_management_suite/
│   ├── api/                          # REST API endpoints
│   ├── boq_management/               # BOQ module
│   │   ├── doctype/boq/
│   │   ├── doctype/boq_item/
│   │   ├── doctype/boq_template/
│   │   └── report/boq_summary/
│   ├── estimation/                   # Rate Analysis & Cost Estimation
│   ├── project_costing/              # Budget, Cost Codes, WIP, Cash Flow
│   ├── site_management/              # Daily Reports, Attendance, SMR
│   ├── progress_billing/             # IPC, Retention Release
│   ├── subcontractor_management/     # Agreements, Work Orders, Payment Certs
│   ├── material_planning/            # Forecasts, Site Transfers, Consumption
│   ├── localization/                 # Regional tax & compliance overrides
│   │   ├── ksa/   uae/   pakistan/   india/   gcc/   africa/   europe/
│   ├── config/                       # Desktop, dashboards
│   ├── fixtures/                     # Workspace, Roles
│   ├── public/css/cms.css            # RTL + Arabic print format styles
│   ├── public/js/cms.js              # Global JS utilities
│   ├── utils/                        # Helpers, permissions, notifications
│   ├── hooks.py                      # App hooks, events, scheduler
│   ├── modules.txt                   # 7 registered modules
│   └── setup.py                      # after_install: roles + custom fields
├── requirements.txt
└── setup.py
```

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

## Contributing

Pull requests welcome. Please open an issue first to discuss what you would like to change.

## Support

For bugs and feature requests, open a [GitHub Issue](https://github.com/Zaryab03/construction_management_suite/issues).
