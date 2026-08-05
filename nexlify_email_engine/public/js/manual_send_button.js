// Adds a persistent "Send Email" button to every document form, independent
// of the Rules Engine. Lets the user pick any Nexlify Email Template scoped
// to the current doctype, edit the message freely, preview it, attach files
// (existing or newly uploaded), schedule the send, request a copy/receipt,
// and optionally attach a Document Print.

frappe.ui.form.on("*", {
	refresh: function (frm) {
		if (frm.is_new()) return;
		add_manual_send_button(frm);
	},
});

function add_manual_send_button(frm) {
	// Avoid duplicate buttons across repeated refreshes
	if (frm.__nexlify_manual_send_added) return;
	frm.__nexlify_manual_send_added = true;

	const $btn = frm.add_custom_button(__("Send Email"), function () {
		open_manual_send_dialog(frm);
	});
	// Style the button black to stand out as the primary send action.
	if ($btn) {
		$btn.removeClass("btn-default").css({
			"background-color": "#000000",
			"color": "#ffffff",
			"border-color": "#000000",
		});
	}
}

function open_manual_send_dialog(frm) {
	frappe.call({
		method: "nexlify_email_engine.nexlify_email_engine.utils.get_templates_for_doctype",
		args: { doctype: frm.doctype },
		callback: function (r) {
			const templates = r.message || [];
			show_send_dialog(frm, templates);
		},
		error: function () {
			frappe.msgprint(__("Could not load email templates for this document type."));
		},
	});
}

function show_send_dialog(frm, templates) {
	const template_options = templates.map(function (t) {
		return { label: t.template_name, value: t.name };
	});

	const d = new frappe.ui.Dialog({
		title: __("Send Email"),
		size: "large",
		fields: [
			{
				fieldtype: "Select",
				fieldname: "template",
				label: __("Choose Template"),
				options: template_options.map(function (t) { return t.value; }),
			},
			{ fieldtype: "Section Break" },
			{
				fieldtype: "Select",
				fieldname: "send_from",
				label: __("From"),
				options: "",
				reqd: 1,
			},
			{
				fieldtype: "Small Text",
				fieldname: "to",
				label: __("To"),
				reqd: 1,
			},
			{
				fieldtype: "Small Text",
				fieldname: "cc",
				label: __("CC"),
				hidden: 1,
			},
			{
				fieldtype: "Small Text",
				fieldname: "bcc",
				label: __("BCC"),
				hidden: 1,
			},
			{ fieldtype: "Section Break" },
			{
				fieldtype: "Data",
				fieldname: "subject",
				label: __("Subject"),
				reqd: 1,
			},
			{
				fieldtype: "Code",
				fieldname: "message",
				label: __("Message"),
				options: "HTML",
			},
			{ fieldtype: "Section Break" },
			{
				fieldtype: "Datetime",
				fieldname: "send_after",
				label: __("Schedule Send At"),
				description: __("Leave blank to send immediately."),
			},
			{ fieldtype: "Column Break" },
			{
				fieldtype: "Check",
				fieldname: "send_me_a_copy",
				label: __("Send me a copy"),
			},
			{
				fieldtype: "Check",
				fieldname: "read_receipt",
				label: __("Send Read Receipt"),
			},
			{ fieldtype: "Section Break" },
			{
				fieldtype: "Check",
				fieldname: "attach_print_pdf",
				label: __("Attach Document Print"),
			},
			{
				fieldtype: "Select",
				fieldname: "print_format",
				label: __("Print Format"),
				depends_on: "eval:doc.attach_print_pdf",
			},
			{ fieldtype: "Column Break" },
			{
				fieldtype: "HTML",
				fieldname: "attachments_display",
			},
		],
		primary_action_label: __("Send"),
		primary_action: function () {
			const values = d.get_values();
			if (!values) return;

			// Disable the button and show a loading state immediately so the
			// user gets feedback that the click registered — without this,
			// slow requests look like the button did nothing, and people
			// click Send repeatedly, sending the email multiple times.
			d.get_primary_btn().prop("disabled", true).text(__("Sending..."));

			function reset_button() {
				d.get_primary_btn().prop("disabled", false).text(__("Send"));
			}

			frappe.call({
				method: "nexlify_email_engine.nexlify_email_engine.utils.send_nexlify_email",
				args: {
					template_name: values.template || null,
					context: JSON.stringify({ doctype: frm.doctype, docname: frm.doc.name }),
					override_to: values.to,
					override_cc: values.cc || "",
					override_bcc: values.bcc || "",
					override_subject: values.subject || "",
					override_from: values.send_from || null,
					override_message: values.message,
					manual_attachments: JSON.stringify(d.__nexlify_manual_attachments || []),
					attach_print_pdf: values.attach_print_pdf ? 1 : 0,
					print_format: values.print_format || null,
					send_after: values.send_after || null,
					send_me_a_copy: values.send_me_a_copy ? 1 : 0,
					read_receipt: values.read_receipt ? 1 : 0,
				},
				callback: function (resp) {
					if (resp.exc) {
						reset_button();
						frappe.msgprint(__("Failed to send email: {0}", [resp.exc]));
						return;
					}
					const result = resp.message;
					if (result.status === "Success") {
						frappe.show_alert({
							message: values.send_after
								? __("Email scheduled successfully")
								: __("Email sent successfully"),
							indicator: "green",
						});
						d.hide();
						frm.reload_doc();
					} else {
						reset_button();
						frappe.msgprint(__("Failed to send email. Check Nexlify Email Log: {0}", [result.log]));
					}
				},
				error: function () {
					reset_button();
					frappe.msgprint(__("Failed to send email. Please check the error log and try again."));
				},
			});
		},
	});

	d.__nexlify_manual_attachments = [];

	// Populate Send From dropdown
	frappe.call({
		method: "frappe.client.get_list",
		args: {
			doctype: "Email Account",
			filters: { enable_outgoing: 1 },
			fields: ["name"],
			limit_page_length: 100,
		},
		callback: function (accts) {
			if (accts.message) {
				const options = accts.message.map(function (a) { return a.name; });
				d.set_df_property("send_from", "options", options);
				d.__nexlify_send_from_options_loaded = true;
				if (d.__nexlify_pending_send_from) {
					d.set_value("send_from", d.__nexlify_pending_send_from);
				}
			}
		},
	});

	// Populate Print Format dropdown for this doctype, always including a
	// "Standard" fallback so there's a sane default even if the doctype has
	// no custom print formats configured.
	frappe.call({
		method: "frappe.client.get_list",
		args: {
			doctype: "Print Format",
			filters: { doc_type: frm.doctype },
			fields: ["name"],
			limit_page_length: 50,
		},
		callback: function (pf) {
			const options = ["Standard"];
			if (pf.message) {
				pf.message.forEach(function (p) {
					if (p.name !== "Standard") options.push(p.name);
				});
			}
			d.set_df_property("print_format", "options", options);
			d.set_value("print_format", "Standard");
		},
	});

	// CC/BCC start hidden behind a small toggle link, matching Frappe's own
	// Compose Email dialog behavior, instead of always taking up space.
	function toggle_cc_bcc() {
		const currently_hidden = d.fields_dict.cc.df.hidden;
		d.fields_dict.cc.df.hidden = currently_hidden ? 0 : 1;
		d.fields_dict.bcc.df.hidden = currently_hidden ? 0 : 1;
		d.fields_dict.cc.refresh();
		d.fields_dict.bcc.refresh();
		const $icon = d.fields_dict.to.$wrapper.find(".nexlify-cc-bcc-arrow i");
		if ($icon.length) {
			$icon.toggleClass("fa-chevron-down fa-chevron-up");
		}
	}

	function render_cc_bcc_toggle() {
		// Toggle button placed inside the To field's own input area, pinned
		// to its top-right corner (sits over the textarea, not above it).
		const $input_wrapper = d.fields_dict.to.$wrapper.find(".control-input-wrapper");
		if ($input_wrapper.length && d.fields_dict.to.$wrapper.find(".nexlify-cc-bcc-arrow").length === 0) {
			$input_wrapper.css("position", "relative");
			const $btn = $(
				'<button type="button" class="btn btn-sm btn-default nexlify-cc-bcc-arrow" ' +
				'style="position: absolute; top: 4px; right: 4px; padding: 3px 9px; z-index: 2;" ' +
				'title="Show/Hide CC and BCC">' +
				'<i class="fa fa-chevron-down" style="font-size: 11px;"></i></button>'
			);
			$btn.on("click", function (e) {
				e.preventDefault();
				toggle_cc_bcc();
			});
			$input_wrapper.append($btn);
		}
	}
	render_cc_bcc_toggle();

	// When a template is chosen, fetch its rendered content for this document
	// and populate the editable fields.
	d.fields_dict.template.df.onchange = function () {
		const template_name = d.get_value("template");
		if (!template_name) return;

		frappe.call({
			method: "nexlify_email_engine.nexlify_email_engine.utils.render_preview",
			args: {
				template_name: template_name,
				context: JSON.stringify({ doctype: frm.doctype, docname: frm.doc.name }),
			},
			callback: function (r) {
				if (r.exc || !r.message) return;
				const preview = r.message;
				d.set_value("to", preview.to || "");
				d.set_value("cc", preview.cc || "");
				d.set_value("bcc", preview.bcc || "");

				["to", "cc", "bcc"].forEach(function (fn) {
					const f = d.fields_dict[fn];
					if (f && f.auto_resize) setTimeout(f.auto_resize, 100);
				});
				d.set_value("subject", preview.subject || "");
				d.set_value("message", preview.message || "");
				if (preview.default_from) {
					d.__nexlify_pending_send_from = preview.default_from;
					if (d.__nexlify_send_from_options_loaded) {
						d.set_value("send_from", preview.default_from);
					}
				}
			},
		});
	};

	// Auto-expand the To/CC/BCC textareas as their content grows, so all
	// recipients stay visible without needing to scroll inside the field.
	["to", "cc", "bcc"].forEach(function (fieldname) {
		const field = d.fields_dict[fieldname];
		if (!field || !field.$input) return;

		const $textarea = field.$input;
		const MIN_HEIGHT = 80;
		$textarea.css({ "min-height": MIN_HEIGHT + "px", "overflow-y": "hidden", "resize": "vertical" });
		function auto_resize() {
			$textarea.css("height", MIN_HEIGHT + "px");
			const needed = $textarea[0].scrollHeight;
			if (needed > MIN_HEIGHT) {
				$textarea.css("height", needed + "px");
			}
		}
		$textarea.on("input change", auto_resize);
		// Re-run after the dialog finishes rendering and whenever we
		// programmatically set a value (e.g. after picking a template).
		setTimeout(auto_resize, 100);
		field.auto_resize = auto_resize;
	});

	// Cap the dialog body height and let it scroll internally instead of
	// growing the whole modal every time a section (e.g. Recipients) opens.
	d.$wrapper.on("shown.bs.modal", function () {
		d.$wrapper.find(".modal-body").css({
			"max-height": "70vh",
			"overflow-y": "auto",
		});
	});

	render_attachments_ui(d, frm);

	// Add a Preview button next to the dialog's primary action
	d.set_secondary_action_label(__("Preview"));
	d.set_secondary_action(function () {
		preview_current_draft(d);
	});

	d.show();

	// Auto-select the template if there's only one
	if (template_options.length === 1) {
		d.set_value("template", template_options[0].value);
		d.fields_dict.template.df.onchange();
	}
}

function preview_current_draft(d) {
	const values = d.get_values(true);
	const preview_d = new frappe.ui.Dialog({
		title: __("Email Preview"),
		size: "large",
		fields: [{ fieldtype: "HTML", fieldname: "preview_iframe" }],
	});

	preview_d.$wrapper.on("shown.bs.modal", function () {
		const field = preview_d.get_field("preview_iframe");
		field.$wrapper.html(
			'<iframe style="width: 100%; height: 500px; border: 1px solid #d1d8dd; border-radius: 4px;"></iframe>'
		);
		const iframe = field.$wrapper.find("iframe")[0];
		if (!iframe || !iframe.contentWindow) return;
		const doc = iframe.contentDocument || iframe.contentWindow.document;
		doc.open();
		doc.write(values.message || "<p>No content</p>");
		doc.close();
	});

	preview_d.show();
}

function render_attachments_ui(d, frm) {
	function refresh_list() {
		const list = d.__nexlify_manual_attachments || [];
		let html = '<div style="font-size: 13px;"><strong>' + __("Attachments") + ':</strong><ul style="margin: 6px 0 8px 18px;">';
		if (list.length === 0) {
			html += '<li style="color: var(--text-muted);">' + __("None") + '</li>';
		} else {
			list.forEach(function (a, idx) {
				html += '<li>' + frappe.utils.escape_html(a.fname) +
					' <a href="#" class="nexlify-remove-attachment" data-idx="' + idx + '">(' + __("remove") + ')</a></li>';
			});
		}
		html += '</ul>' +
			'<button type="button" class="btn btn-xs btn-default" id="nexlify-attach-existing">' + __("Choose Existing File") + '</button> ' +
			'<button type="button" class="btn btn-xs btn-default" id="nexlify-attach-upload">' + __("Upload New File") + '</button>' +
			'</div>';

		d.fields_dict.attachments_display.$wrapper.html(html);

		d.fields_dict.attachments_display.$wrapper.find(".nexlify-remove-attachment").on("click", function (e) {
			e.preventDefault();
			const idx = $(this).data("idx");
			d.__nexlify_manual_attachments.splice(idx, 1);
			refresh_list();
		});

		d.fields_dict.attachments_display.$wrapper.find("#nexlify-attach-existing").on("click", function () {
			frappe.call({
				method: "frappe.client.get_list",
				args: {
					doctype: "File",
					filters: { attached_to_doctype: frm.doctype, attached_to_name: frm.doc.name },
					fields: ["name", "file_name", "file_url"],
					limit_page_length: 50,
				},
				callback: function (r) {
					const files = r.message || [];
					if (files.length === 0) {
						frappe.msgprint(__("No existing files attached to this document."));
						return;
					}
					const pick_d = new frappe.ui.Dialog({
						title: __("Choose Existing File"),
						fields: [
							{
								fieldtype: "MultiCheck",
								fieldname: "files",
								options: files.map(function (f) {
									return { label: f.file_name, value: f.name, description: f.file_url };
								}),
							},
						],
						primary_action_label: __("Add"),
						primary_action: function () {
							const selected = pick_d.get_values().files || [];
							selected.forEach(function (fname) {
								const file_doc = files.find(function (f) { return f.name === fname; });
								if (file_doc) {
									d.__nexlify_manual_attachments.push(file_doc.name);
								}
							});
							refresh_list();
							pick_d.hide();
						},
					});
					pick_d.show();
				},
			});
		});

		d.fields_dict.attachments_display.$wrapper.find("#nexlify-attach-upload").on("click", function () {
			new frappe.ui.FileUploader({
				doctype: frm.doctype,
				docname: frm.doc.name,
				on_success: function (file_doc) {
					d.__nexlify_manual_attachments.push(file_doc.name);
					refresh_list();
				},
			});
		});
	}

	refresh_list();
}
