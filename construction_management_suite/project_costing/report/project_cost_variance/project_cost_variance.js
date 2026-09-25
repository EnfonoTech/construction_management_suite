/**
 * Budget against actual, down the cost breakdown.
 *
 * The filters were already here. What is new is the tree: `Cost Code` nests,
 * so a heading shows what everything beneath it comes to and opens to show
 * why. Before this the headings and their own children sat side by side as
 * peers and the two never added up.
 */
frappe.query_reports["Project Cost Variance"] = {
    filters: [
        {
            fieldname: "company",
            label: __("Company"),
            fieldtype: "Link",
            options: "Company",
            default: frappe.defaults.get_user_default("Company"),
            reqd: 1,
        },
        {
            fieldname: "project",
            label: __("Project"),
            fieldtype: "Link",
            options: "Project",
            get_query: () => ({
                filters: { company: frappe.query_report.get_filter_value("company") },
            }),
        },
    ],

    // The cost breakdown nests, so the report does too: a heading shows what
    // everything beneath it comes to, and opens to show why.
    tree: true,
    name_field: "cost_code",
    parent_field: "parent_cost_code",
    initial_depth: 1,

    formatter(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        if (data && data.is_group) {
            value = value.bold();
        }
        // Overspend is the only thing anybody opens this report to find.
        if (data && column.fieldname === "variance" && flt(data.variance) < 0) {
            value = `<span style="color:var(--red-500)">${value}</span>`;
        }
        if (data && column.fieldname === "utilization_pct" && flt(data.utilization_pct) > 100) {
            value = `<span style="color:var(--red-500);font-weight:600">${value}</span>`;
        }
        return value;
    },
};
