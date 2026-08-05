frappe.ui.form.on("Nexlify Email Rule", {
	refresh: function (frm) {
		if (frm.is_new()) return;
		if (!frm.doc.reference_doctype) return;
		if (!frm.doc.actions || frm.doc.actions.length === 0) return;

		frm.add_custom_button(__("Test / Preview"), function () {
			open_test_preview_dialog(frm);
		});
	},
});

function open_test_preview_dialog(frm) {
	const d = new frappe.ui.Dialog({
		title: __("Test Rule Condition"),
		fields: [
			{
				fieldtype: "Link",
				fieldname: "test_document",
				label: __("Pick a {0} to test against", [frm.doc.reference_doctype]),
				options: frm.doc.reference_doctype,
				reqd: 1,
			},
			{ fieldtype: "Section Break" },
			{
				fieldtype: "HTML",
				fieldname: "result_display",
			},
		],
		primary_action_label: __("Run Test"),
		primary_action: function () {
			const docname = d.get_value("test_document");
			if (!docname) return;

			frappe.call({
				method: "nexlify_email_engine.nexlify_email_engine.utils.test_rule_condition",
				args: {
					rule_name: frm.doc.name,
					docname: docname,
				},
				callback: function (r) {
					if (r.exc || !r.message) return;
					const result = r.message;
					const $wrapper = d.fields_dict.result_display.$wrapper;

					if (!result.condition_met) {
						$wrapper.html(
							'<div style="padding: 10px; background: #fff7e6; border-radius: 4px; color: #915400;">' +
							__("Condition NOT met for this document — no email would be sent.") +
							'</div>'
						);
						return;
					}

					$wrapper.html(
						'<div style="padding: 10px; background: #e6ffed; border-radius: 4px; color: #036b26; margin-bottom: 10px;">' +
						__("Condition met — this is what would be sent:") +
						'</div>' +
						'<div style="border: 1px solid #e5e7eb; border-radius: 6px; padding: 12px 16px; background: #f9fafb; font-size: 13px; line-height: 1.9; margin-bottom: 10px;">' +
						'<div><strong>' + __("From") + ':</strong> ' + frappe.utils.escape_html(result.sender || "—") + '</div>' +
						'<div><strong>' + __("To") + ':</strong> ' + frappe.utils.escape_html(result.to || "—") + '</div>' +
						'<div><strong>' + __("CC") + ':</strong> ' + frappe.utils.escape_html(result.cc || "—") + '</div>' +
						'<div><strong>' + __("BCC") + ':</strong> ' + frappe.utils.escape_html(result.bcc || "—") + '</div>' +
						'<div><strong>' + __("Subject") + ':</strong> ' + frappe.utils.escape_html(result.subject || "—") + '</div>' +
						'</div>' +
						'<iframe style="width: 100%; height: 350px; border: 1px solid #d1d8dd; border-radius: 4px;"></iframe>'
					);

					const iframe = $wrapper.find("iframe")[0];
					if (iframe) {
						const doc = iframe.contentDocument || iframe.contentWindow.document;
						doc.open();
						doc.write(result.message || "<p>No content</p>");
						doc.close();
					}
				},
			});
		},
	});

	d.show();
}
