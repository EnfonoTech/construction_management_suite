# Calculation parity check

The figures on screen are computed in the browser (`public/js/cms.js` → `CMS.calc`)
and again on the server when the document saves. If those two drift, a user
commits to a number the system then changes underneath them.

This harness runs both on identical input and compares every computed field.

```bash
cd construction_management_suite/tests
node client.js ../public/js/cms.js > /tmp/client.json
cd /home/ramees/frappe-bench/sites && ../env/bin/python <app>/tests/server.py > /tmp/server.json
# then diff the two, tolerance 0.0005
```

`cases.json` deliberately includes the awkward inputs: zero contract quantity
(division guard), a fully-completed line, a subcontract resource with a waste
factor, over-ordered material, and overtime.

Server-owned fields are excluded from comparison — `already_ordered_qty` on a
Material Forecast is read from open Purchase Orders and is read-only on the
form, so the browser can only ever use what the server last put there.
