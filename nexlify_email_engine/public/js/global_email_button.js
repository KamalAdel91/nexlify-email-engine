frappe.call({
    method: "nexlify_email_engine.nexlify_email_engine.utils.get_doctypes_with_rules",
    callback: function (r) {
        if (!r.message) return;
        const doctypes = r.message; // backend already returns distinct values

        doctypes.forEach(function (dt) {
            frappe.ui.form.on(dt, {
                refresh: function (frm) {
                    add_nexlify_email_rule_buttons(frm);
                }
            });
        });
    },
    error: function () {
        console.warn("Nexlify Email Engine: failed to fetch doctypes with rules.");
    }
});

function add_nexlify_email_rule_buttons(frm) {
    if (frm.is_new()) return;

    frappe.call({
        method: "nexlify_email_engine.nexlify_email_engine.utils.get_matching_rules",
        args: { doctype: frm.doctype, docname: frm.doc.name },
        callback: function (r) {
            if (r.exc || !r.message) return;

            // Remove any Nexlify buttons added on a previous refresh of this
            // form to avoid the same button appearing multiple times.
            if (frm.__nexlify_button_labels) {
                frm.__nexlify_button_labels.forEach(function (label) {
                    frm.remove_custom_button(label);
                });
            }
            frm.__nexlify_button_labels = [];

            r.message.forEach(function (rule) {
                const label = rule.button_label || __("Send Email");
                frm.add_custom_button(label, function () {
                    handle_rule_button_click(frm, rule);
                });
                frm.__nexlify_button_labels.push(label);
            });
        },
        error: function () {
            console.warn("Nexlify Email Engine: failed to fetch matching rules for", frm.doctype);
        }
    });
}

function handle_rule_button_click(frm, rule) {
    var email_action = null;
    for (var i = 0; i < rule.actions.length; i++) {
        if (rule.actions[i].action_type === "Send Email" && rule.actions[i].email_template) {
            email_action = rule.actions[i];
            break;
        }
    }
    if (!email_action) {
        frappe.msgprint(__("No Send Email action found in this rule."));
        return;
    }

    var context = { doctype: frm.doctype, docname: frm.doc.name };

    if (rule.requires_extra_input && rule.extra_input_fields && rule.extra_input_fields.length > 0) {
        var prompt_fields = rule.extra_input_fields.map(function (f) {
            return {
                fieldname: f.fieldname,
                label: f.label || f.fieldname,
                fieldtype: f.fieldtype,
                reqd: f.reqd ? 1 : 0,
            };
        });
        frappe.prompt(
            prompt_fields,
            function (values) {
                Object.keys(values).forEach(function (key) { context[key] = values[key]; });
                nexlify_email.preview_and_send(email_action.email_template, context, {});
            },
            __("Extra Input Required"),
            __("Continue to Preview")
        );
    } else {
        nexlify_email.preview_and_send(email_action.email_template, context, {});
    }
}
