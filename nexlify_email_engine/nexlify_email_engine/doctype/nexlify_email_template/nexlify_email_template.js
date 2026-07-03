frappe.ui.form.on("Nexlify Email Template", {
    refresh: function (frm) {
        // Add "Show Available Fields" button in the toolbar
        frm.add_custom_button(__("Show Available Fields"), function () {
            show_available_fields(frm);
        });
    },
});

function show_available_fields(frm) {
    const ref_doctype = frm.doc.reference_doctype;
    if (!ref_doctype) {
        frappe.msgprint(__("Please set a Reference DocType first."));
        return;
    }

    frappe.call({
        method: "frappe.client.get_list",
        args: {
            doctype: "DocField",
            filters: {
                parent: ref_doctype,
            },
            fields: ["fieldname", "label", "fieldtype"],
            order_by: "idx asc",
            limit_page_length: 500,
        },
        callback: function (r) {
            if (!r.message || r.message.length === 0) {
                frappe.msgprint(__("No fields found for {0}.", [ref_doctype]));
                return;
            }

            const fields = r.message;
            const d = new frappe.ui.Dialog({
                title: __("Available Fields for {0}", [ref_doctype]),
                size: "large",
                fields: [
                    {
                        fieldtype: "HTML",
                        fieldname: "field_list",
                    },
                    {
                        fieldtype: "Small Text",
                        fieldname: "insert_template",
                        label: __("Insert Template"),
                        description: __("Click a field above, or type {{ doc.fieldname }} manually"),
                    },
                ],
                primary_action_label: __("Insert into Message"),
                primary_action: function () {
                    const val = d.get_value("insert_template");
                    if (val) {
                        frm.set_value("message", (frm.doc.message || "") + "\n" + val);
                    }
                    d.hide();
                },
            });

            // Build the field list HTML
            let html =
                '<div style="max-height: 300px; overflow-y: auto; border: 1px solid #d1d8dd; padding: 10px; border-radius: 4px;">';
            html += '<table class="table table-bordered table-hover" style="margin-bottom: 0;">';
            html += "<tr><th>Field Name</th><th>Label</th><th>Type</th><th>Insert</th></tr>";

            fields.forEach(function (f) {
                const escaped_fieldname = frappe.utils.escape_html(f.fieldname);
                html += `<tr>
                    <td><code>${escaped_fieldname}</code></td>
                    <td>${frappe.utils.escape_html(f.label || f.fieldname)}</td>
                    <td>${frappe.utils.escape_html(f.fieldtype)}</td>
                    <td>
                        <button class="btn btn-xs btn-default insert-field-btn" 
                            data-fieldname="${escaped_fieldname}">
                            <i class="fa fa-plus"></i>
                        </button>
                    </td>
                </tr>`;
            });

            html += "</table></div>";

            d.set_value("field_list", html);
            d.show();

            // After dialog is shown, bind click handlers to the buttons
            setTimeout(function () {
                d.$body.find("button.insert-field-btn").on("click", function () {
                    const fieldname = $(this).data("fieldname");
                    const current = d.get_value("insert_template") || "";
                    d.set_value(
                        "insert_template",
                        current + "{{ doc." + fieldname + " }}"
                    );
                });
            }, 100);
        },
    });
}