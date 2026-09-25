/**
 * The cost breakdown as the desk shows it: a tree, filtered to one company.
 *
 * A chart has several roots — one heading per section of the works, plus the
 * standalone leaves like Preliminaries that hang off nothing — so the view is
 * rooted on "All Cost Codes" rather than on a single node.
 */
frappe.treeview_settings["Cost Code"] = {
    get_tree_nodes: "construction_management_suite.project_costing.doctype.cost_code.cost_code.get_children",
    add_tree_node: "construction_management_suite.project_costing.doctype.cost_code.cost_code.add_node",
    filters: [
        {
            fieldname: "company",
            fieldtype: "Link",
            options: "Company",
            label: __("Company"),
            default: frappe.defaults.get_user_default("Company"),
        },
    ],
    root_label: "All Cost Codes",
    get_label(node) {
        return `${node.data.value} — ${frappe.utils.escape_html(node.data.title || "")}`;
    },
    onload(treeview) {
        treeview.make_tree();
    },
};
