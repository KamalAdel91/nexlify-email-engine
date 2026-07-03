import frappe
import json


@frappe.whitelist()
def render_preview(template_name, context):
	"""
	Render an email template with context and return the rendered fields.

	Args:
	    template_name (str): Name of the Nexlify Email Template
	    context (dict | str): JSON dict with optional 'doctype'/'docname' and arbitrary keys

	Returns:
	    dict: Rendered subject, message, to, cc, bcc
	"""
	if isinstance(context, str):
		context = json.loads(context)

	template = frappe.get_doc("Nexlify Email Template", template_name)

	# Build Jinja context
	jinja_context = {}

	# If doctype+docname provided, fetch the document and expose as `doc`
	if context.get("doctype") and context.get("docname"):
		jinja_context["doc"] = frappe.get_doc(context["doctype"], context["docname"])

	# Merge any extra explicit keys from context (overwrites `doc` if provided directly)
	for key, value in context.items():
		if key not in ("doctype", "docname"):
			jinja_context[key] = value

	# Render fields using Jinja
	rendered_subject = frappe.render_template(template.subject, jinja_context) if template.subject else ""
	rendered_message = frappe.render_template(template.message, jinja_context) if template.message else ""
	rendered_to = frappe.render_template(template.default_to, jinja_context) if template.default_to else ""
	rendered_cc = frappe.render_template(template.default_cc, jinja_context) if template.default_cc else ""
	rendered_bcc = frappe.render_template(template.default_bcc, jinja_context) if template.default_bcc else ""

	return {
		"subject": rendered_subject,
		"message": rendered_message,
		"to": rendered_to,
		"cc": rendered_cc,
		"bcc": rendered_bcc,
		"default_from": template.default_from,
	}


@frappe.whitelist()
def send_nexlify_email(template_name, context, override_to=None):
	"""
	Render and send an email using the given template and context.

	Args:
	    template_name (str): Name of the Nexlify Email Template
	    context (dict | str): JSON dict with optional 'doctype'/'docname' and arbitrary keys
	    override_to (str, optional): Override recipient email(s)

	Returns:
	    dict: Status of the send operation
	"""
	if isinstance(context, str):
		context = json.loads(context)

	template = frappe.get_doc("Nexlify Email Template", template_name)

	# Build Jinja context
	jinja_context = {}
	if context.get("doctype") and context.get("docname"):
		jinja_context["doc"] = frappe.get_doc(context["doctype"], context["docname"])

	for key, value in context.items():
		if key not in ("doctype", "docname"):
			jinja_context[key] = value

	# Render fields
	rendered_subject = frappe.render_template(template.subject, jinja_context) if template.subject else ""
	rendered_message = frappe.render_template(template.message, jinja_context) if template.message else ""
	rendered_to = frappe.render_template(template.default_to, jinja_context) if template.default_to else ""
	rendered_cc = frappe.render_template(template.default_cc, jinja_context) if template.default_cc else ""
	rendered_bcc = frappe.render_template(template.default_bcc, jinja_context) if template.default_bcc else ""

	# Build recipients
	recipients = override_to if override_to else rendered_to
	if isinstance(recipients, str):
		recipients = [r.strip() for r in recipients.split(",") if r.strip()]

	cc_list = [r.strip() for r in rendered_cc.split(",") if r.strip()] if rendered_cc else None
	bcc_list = [r.strip() for r in rendered_bcc.split(",") if r.strip()] if rendered_bcc else None

	# Determine sender
	sender = None
	if template.default_from:
		email_account = frappe.get_doc("Email Account", template.default_from)
		sender = email_account.email_id

	# Build attachments from static_attachments child table
	attachments = []
	for row in template.static_attachments:
		if row.attachment:
			try:
				file_data = frappe.get_doc("File", {"file_url": row.attachment})
				attachments.append({
					"fid": file_data.name,
					"file_url": row.attachment,
				})
			except frappe.DoesNotExistError:
				attachments.append({"file_url": row.attachment})

	# Send the email
	log = frappe.new_doc("Nexlify Email Log")
	log.template = template_name
	log.reference_doctype = context.get("doctype", "")
	log.reference_name = context.get("docname", "")

	try:
		frappe.sendmail(
			recipients=recipients,
			cc=cc_list,
			bcc=bcc_list,
			sender=sender,
			subject=rendered_subject,
			message=rendered_message,
			attachments=attachments,
			now=True,
		)
		log.status = "Success"
	except Exception as e:
		frappe.log_error(f"Nexlify Email Engine: Failed to send email using template '{template_name}'", exc=e)
		log.status = "Failed"
		log.error_message = str(e)

	log.sent_at = frappe.utils.now_datetime()
	log.insert(ignore_permissions=True)

	return {"status": log.status, "log": log.name}