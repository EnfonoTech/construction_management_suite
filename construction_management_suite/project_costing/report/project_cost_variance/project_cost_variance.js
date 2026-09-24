// The server has read these filters since the report was written; there was
// simply no way to set them from the desk, so it always ran across every job
// on the site.
frappe.query_reports["Project Cost Variance"] = {
    filters: [
        {
            fieldname: "company",
            label: __("Company"),
            fieldtype: "Link",
            options: "Company",
            default: frappe.defaults.get_user_default("Company"),
        },
        {
            fieldname: "project",
            label: __("Project"),
            fieldtype: "Link",
            options: "Project",
            get_query: () => ({ filters: { company: frappe.query_report.get_filter_value("company") } }),
        },
    ],
    formatter(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        // Overspend is the only thing anybody opens this report to find.
        if (column.fieldname === "variance" && data && flt(data.variance) < 0) {
            value = `<span style="color:var(--red-500)">${value}</span>`;
        }
        if (column.fieldname === "utilization_pct" && data && flt(data.utilization_pct) > 100) {
            value = `<span style="color:var(--red-500);font-weight:600">${value}</span>`;
        }
        return value;
    },
};
