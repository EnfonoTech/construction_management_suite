frappe.query_reports["Material Position"] = {
    filters: [
        {
            fieldname: "project", label: __("Project"), fieldtype: "Link",
            options: "Project", reqd: 1,
            default: frappe.defaults.get_user_default("Project"),
        },
        {
            fieldname: "boq", label: __("BOQ"), fieldtype: "Link", options: "BOQ",
            get_query: () => ({
                filters: {
                    docstatus: 1,
                    project: frappe.query_report.get_filter_value("project"),
                },
            }),
        },
        { fieldname: "item_group", label: __("Item Group"), fieldtype: "Link", options: "Item Group" },
        {
            // Blank keeps what this report has always shown — material only,
            // which is what a store issues. The rest are real costs against a
            // line of work that nobody buys into a warehouse.
            fieldname: "resource_type", label: __("Resource Type"), fieldtype: "Select",
            options: ["", "All", "Material", "Labour", "Equipment", "Subcontract", "Overhead"].join("\n"),
        },
        { fieldname: "by_work", label: __("Split by work"), fieldtype: "Check" },
        { fieldname: "only_over", label: __("Over-consumed only"), fieldtype: "Check" },
    ],

    formatter(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        if (!data) return value;
        // Consuming more than the bill allows, or paying more than it assumed.
        if (column.fieldname === "consumed" && flt(data.consumed) > flt(data.required) + 0.0001) {
            value = `<span style="color: var(--red-500); font-weight: 600">${value}</span>`;
        }
        if (column.fieldname === "balance" && flt(data.balance) < 0) {
            value = `<span style="color: var(--red-500)">${value}</span>`;
        }
        if (column.fieldname === "rate_variance_percent" && flt(data.rate_variance_percent) > 0) {
            value = `<span style="color: var(--red-500)">${value}</span>`;
        }
        return value;
    },
};
