/**
 * nexlify_email.preview_and_send
 *
 * A global reusable function to preview and send emails using Nexlify Email Templates.
 * Available site-wide via app_include_js in hooks.py.
 *
 * Usage:
 *   nexlify_email.preview_and_send("Template Name", { doctype: "Sales Invoice", docname: "INV-0001" }, {});
 *
 * @param {string} template_name - Name of the Nexlify Email Template
 * @param {object} context - Context dict (may include doctype/docname + extra keys)
 * @param {object} options - Optional overrides { override_to: "email@example.com" }
 */
var nexlify_email = nexlify_email || {};

nexlify_email.preview_and_send = function (template_name, context, options) {
    options = options || {};

    // Step 1: Call render_preview
    frappe.call({
        method: "nexlify_email_engine.nexlify_email_engine.utils.render_preview",
        args: {
            template_name: template_name,
            context: context,
        },
        callback: function (r) {
            if (r.exc) {
                frappe.msgprint(__("Error rendering preview: {0}", [r.exc]));
                return;
            }

            const preview = r.message;

            // Step 2: Build the dialog
            const dialog = new frappe.ui.Dialog({
                title: __("Email Preview — {0}", [template_name]),
                size: "large",
                fields: [
                    {
                        fieldtype: "Section Break",
                        label: __("Recipients"),
                    },
                    {
                        fieldtype: "Select",
                        fieldname: "send_from",
                        label: __("Send From"),
                        options: "",
                        default: preview.default_from || "",
                    },
                    {
                        fieldtype: "Data",
                        fieldname: "to",
                        label: __("To"),
                        default: preview.to || "",
                        reqd: 1,
                    },
                    {
                        fieldtype: "Data",
                        fieldname: "cc",
                        label: __("CC"),
                        default: preview.cc || "",
                    },
                    {
                        fieldtype: "Data",
                        fieldname: "bcc",
                        label: __("BCC"),
                        default: preview.bcc || "",
                    },
                    {
                        fieldtype: "Data",
                        fieldname: "subject",
                        label: __("Subject"),
                        default: preview.subject || "",
                        reqd: 1,
                    },
                    {
                        fieldtype: "Section Break",
                        label: __("HTML Preview"),
                    },
                    {
                        fieldtype: "HTML",
                        fieldname: "preview_iframe",
                    },
                    {
                        fieldtype: "Section Break",
                        label: __("Raw HTML"),
                    },
                    {
                        fieldtype: "Code",
                        fieldname: "raw_html",
                        label: __("HTML Message"),
                        options: "HTML",
                        default: preview.message || "",
                    },
                    {
                        fieldtype: "Button",
                        fieldname: "refresh_preview",
                        label: __("Refresh Preview"),
                    },
                ],
                primary_action_label: __("Confirm & Send"),
                primary_action: function () {
                    const values = dialog.get_values();
                    if (!values) return;

                    frappe.confirm(
                        __("Are you sure you want to send this email?"),
                        function () {
                            // Call send_nexlify_email with edited values
                            frappe.call({
                                method: "nexlify_email_engine.nexlify_email_engine.utils.send_nexlify_email",
                                args: {
                                    template_name: template_name,
                                    context: context,
                                    override_to: options.override_to || null,
                                },
                                callback: function (resp) {
                                    if (resp.exc) {
                                        frappe.msgprint(__("Failed to send email: {0}", [resp.exc]));
                                        return;
                                    }

                                    const result = resp.message;
                                    if (result.status === "Success") {
                                        frappe.show_alert({
                                            message: __("Email sent successfully (Log: {0})", [result.log]),
                                            indicator: "green",
                                        });
                                        dialog.hide();
                                    } else {
                                        frappe.msgprint(__("Failed to send email. Check Nexlify Email Log: {0}", [result.log]));
                                    }
                                },
                            });
                        }
                    );
                },
            });

            // --- Populate Email Account options ---
            frappe.call({
                method: "frappe.client.get_list",
                args: {
                    doctype: "Email Account",
                    filters: { enable_outgoing: 1 },
                    fields: ["name", "email_id"],
                    limit_page_length: 100,
                },
                callback: function (accts) {
                    if (accts.message) {
                        const options = accts.message.map(function (a) {
                            return a.name;
                        });
                        dialog.set_df_property("send_from", "options", options);
                        if (!dialog.get_value("send_from") && options.length > 0) {
                            dialog.set_value("send_from", options[0]);
                        }
                    }
                },
            });

            // --- Render initial preview ---
            function render_preview_iframe(html_content) {
                const wrapper = dialog.get_field("preview_iframe").$wrapper;
                wrapper.html(
                    '<iframe id="email-preview-iframe" style="width: 100%; height: 400px; border: 1px solid #d1d8dd; border-radius: 4px;"></iframe>'
                );

                const iframe = wrapper.find("iframe")[0];
                const doc = iframe.contentDocument || iframe.contentWindow.document;
                doc.open();
                doc.write(html_content);
                doc.close();
            }

            render_preview_iframe(preview.message);

            // --- Refresh Preview button ---
            dialog.get_field("refresh_preview").df.onclick = function () {
                const html_val = dialog.get_value("raw_html");
                render_preview_iframe(html_val);
            };

            dialog.show();
        },
    });
};