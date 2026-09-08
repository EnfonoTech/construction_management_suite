// Load the real cms.js the browser loads, with just enough of Frappe stubbed.
const fs = require("fs");
global.window = global;   // in a browser, window IS the global object
global.flt = (v) => { const n = parseFloat(v); return isNaN(n) ? 0 : n; };
global.__ = (s) => s;
global.$ = () => ({ append: () => {}, html: () => {}, css: () => {} });
global.format_currency = (v) => String(v);
global.locals = {};
global.frappe = {
  ui: { form: { on: () => {} } },
  db: { get_value: () => Promise.resolve({}) },
  call: () => {}, msgprint: () => {}, new_doc: () => {},
  defaults: { get_default: () => "OMR" },
  datetime: { get_today: () => "2026-09-08" },
  set_route: () => {}, show_alert: () => {},
  model: { set_value: () => {} },
};
eval(fs.readFileSync(process.argv[2], "utf8"));
const lib = global.CMS;

const cases = JSON.parse(fs.readFileSync("cases.json", "utf8"));
const out = {};
for (const [dt, doc] of Object.entries(cases)) {
  const d = JSON.parse(JSON.stringify(doc));
  d.doctype = dt;
  lib.calc[dt](d);
  out[dt] = d;
}
console.log(JSON.stringify(out));
