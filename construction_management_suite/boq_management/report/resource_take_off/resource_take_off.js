frappe.query_reports["Resource Take-off"] = {
    filters: [
        {
            // The estimate is what the job is bought and built against, and a
            // job with no BOQ at all is the normal case — so that is the default.
            fieldname: "source",
            label: __("Priced From"),
            fieldtype: "Select",
            options: ["Cost Estimation", "BOQ"].join("\n"),
            default: "Cost Estimation",
            reqd: 1,
        },
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
        {
            fieldname: "boq",
            label: __("BOQ"),
            fieldtype: "Link",
            options: "BOQ",
            depends_on: 'eval:doc.source == "BOQ"',
            get_query: () => {
                const filters = { docstatus: ["<", 2] };
                const project = frappe.query_report.get_filter_value("project");
                const company = frappe.query_report.get_filter_value("company");
                if (project) filters.project = project;
                if (company) filters.company = company;
                return { filters };
            },
        },
        {
            fieldname: "resource_type",
            label: __("Resource Type"),
            fieldtype: "Select",
            options: ["", "Material", "Labour", "Equipment", "Subcontract", "Overhead"],
        },
        {
            fieldname: "item_group",
            label: __("Item Group"),
            fieldtype: "Link",
            options: "Item Group",
        },
        {
            fieldname: "group_by",
            label: __("Group By"),
            fieldtype: "Select",
            options: [
                "",
                "BOQ Item",
                "Resource Item",
                "Item Group",
                "Resource Type",
                "Work Category",
                "BOQ Section",
            ],
        },
        {
            fieldname: "show_resources",
            label: __("Explode into resources"),
            fieldtype: "Check",
            default: 1,
        },
    ],

    formatter(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        if (data && data.bold) {
            value = value.bold();
        }
        // A line with no rate analysis is a gap in the take-off, not a zero.
        if (data && data.source === "No analysis" && column.fieldname === "resource_description") {
            value = `<span style="color: var(--text-muted)">${value}</span>`;
        }
        return value;
    },

    onload(report) {
        report.page.add_inner_button(__("Material Only"), () => {
            frappe.query_report.set_filter_value({
                resource_type: "Material",
                group_by: "Resource Item",
            });
        });
    },
};
