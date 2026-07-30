frappe.ui.form.on("Nexlify Email Template", {
    refresh: function (frm) {
        frm.add_custom_button(__("Show Available Fields"), function () {
            show_available_fields(frm);
        });

        if (frm.doc.message) {
            frm.add_custom_button(__("Preview"), function () {
                preview_template(frm);
            });
        }

        setup_recipient_pickers(frm);
    },
});

// --- Cached full list of active user emails, fetched once per page load ---
let _cached_user_emails = null;

function get_all_active_user_emails() {
    if (_cached_user_emails) return _cached_user_emails;

    let data = [];
    frappe.call({
        method: "frappe.client.get_list",
        args: {
            doctype: "User",
            filters: [["enabled", "=", 1]],
            fields: ["email"],
            limit_page_length: 500,
            order_by: "full_name asc",
        },
        async: false,
        callback: function (r) {
            if (r.message) {
                data = r.message.map(function (u) { return u.email; }).filter(Boolean);
            }
        },
    });

    _cached_user_emails = data;
    return data;
}

function get_email_suggestions(txt) {
    const all_emails = get_all_active_user_emails();
    if (!txt) return all_emails;
    const lower = txt.toLowerCase();
    return all_emails.filter(function (e) { return e.toLowerCase().indexOf(lower) !== -1; });
}

// --- "Select Recipients" pickers for the actual Default To/CC/BCC fields on the form ---
function setup_recipient_pickers(frm) {
    ["default_to", "default_cc", "default_bcc"].forEach(function (fieldname) {
        const field = frm.fields_dict[fieldname];
        if (!field || !field.$wrapper) return;

        if (field.$wrapper.next(".recipient-picker-wrapper").length > 0) return; // already added

        const $wrapper = $('<div class="recipient-picker-wrapper" style="margin-top: 8px; margin-bottom: 16px;"></div>');
        const $btn = $(
            '<button type="button" class="btn btn-xs btn-default">' +
            __("Select Recipients") +
            "</button>"
        );

        $btn.on("click", function () {
            open_recipient_picker(frm, fieldname);
        });

        $wrapper.append($btn);
        field.$wrapper.after($wrapper);
    });
}

function open_recipient_picker(frm, fieldname) {
    const current_value = frm.doc[fieldname] || "";
    const current_list = current_value.split(",").map(function (v) { return v.trim(); }).filter(Boolean);

    const d = new frappe.ui.Dialog({
        title: __("Select Recipients"),
        fields: [
            {
                fieldtype: "MultiSelect",
                fieldname: "recipients",
                label: __("Emails"),
                get_data: get_email_suggestions,
            },
        ],
        primary_action_label: __("Apply"),
        primary_action: function () {
            const control = d.fields_dict.recipients;
            const values = control.get_values ? control.get_values() : [];
            frm.set_value(fieldname, values.join(", "));
            frm.dirty();
            d.hide();
            frappe.show_alert({ message: __("Recipients updated"), indicator: "green" });
        },
    });

    d.show();

    // This MultiSelect control keeps its state as a plain comma-separated
    // string in the underlying $input, not as discrete "pills" — there is
    // no add_pill/set_value API. Prefill by writing directly into $input
    // and firing "input" so the control's internal parsing picks it up.
    requestAnimationFrame(function () {
        const control = d.fields_dict.recipients;
        if (control && control.$input && current_list.length > 0) {
            control.$input.val(current_list.join(", "));
            control.$input.trigger("input");
        }
    });
}

function preview_template(frm) {
    if (frm.is_dirty()) {
        frappe.msgprint(__("Please save the template first to preview the latest changes."));
        return;
    }

    frappe.call({
        method: "nexlify_email_engine.nexlify_email_engine.utils.render_preview",
        args: {
            template_name: frm.doc.name,
            context: {},
        },
        callback: function (r) {
            if (r.exc || !r.message) {
                frappe.msgprint(__("Error rendering preview."));
                return;
            }

            const preview = r.message;

            function escape(val) {
                return frappe.utils.escape_html(val || "—");
            }

            const d = new frappe.ui.Dialog({
                title: __("Email Preview — {0}", [frm.doc.name]),
                size: "large",
                fields: [
                    {
                        fieldtype: "HTML",
                        fieldname: "meta_display",
                    },
                    { fieldtype: "Section Break", label: __("HTML Preview") },
                    {
                        fieldtype: "HTML",
                        fieldname: "preview_iframe",
                    },
                ],
            });

            let meta_html = '<div style="border: 1px solid #e5e7eb; border-radius: 6px; padding: 12px 16px; background: #f9fafb; font-size: 13px; line-height: 1.9;">';
            meta_html += "<div><strong>" + __("From") + ":</strong> " + escape(preview.default_from) + "</div>";
            meta_html += "<div><strong>" + __("To") + ":</strong> " + escape(preview.to) + "</div>";
            meta_html += "<div><strong>" + __("CC") + ":</strong> " + escape(preview.cc) + "</div>";
            meta_html += "<div><strong>" + __("BCC") + ":</strong> " + escape(preview.bcc) + "</div>";
            meta_html += "<div><strong>" + __("Subject") + ":</strong> " + escape(preview.subject) + "</div>";

            if (preview.attachments && preview.attachments.length > 0) {
                const list_html = preview.attachments.map(function (a) {
                    return '<li><a href="' + a.file_url + '" target="_blank">' + frappe.utils.escape_html(a.file_name) + '</a></li>';
                }).join("");
                meta_html += '<div style="margin-top: 6px;"><strong>' + __("Attachments") + ':</strong><ul style="margin: 4px 0 0 18px;">' + list_html + '</ul></div>';
            }

            meta_html += "</div>";

            d.fields_dict.meta_display.$wrapper.html(meta_html);

            d.$wrapper.on("shown.bs.modal", function () {
                render_preview_iframe(d, preview.message);
            });

            d.show();
        },
    });
}

function render_preview_iframe(dialog, html_content) {
    const field = dialog.get_field("preview_iframe");
    if (!field || !field.$wrapper) return;

    field.$wrapper.html(
        '<iframe style="width: 100%; height: 500px; border: 1px solid #d1d8dd; border-radius: 4px;"></iframe>'
    );

    const iframe = field.$wrapper.find("iframe")[0];
    if (!iframe || !iframe.contentWindow) return;

    const doc = iframe.contentDocument || iframe.contentWindow.document;
    doc.open();
    doc.write(html_content || "<p>No content</p>");
    doc.close();
}

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
                primary_action_label: __("Copy to Clipboard"),
                primary_action: function () {
                    const val = d.get_value("insert_template");
                    if (val) {
                        navigator.clipboard.writeText(val);
                        frappe.show_alert({ message: __("Copied to clipboard"), indicator: "green" });
                    }
                    d.hide();
                },
            });

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
