import frappe
import json
from frappe.utils import cint


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

	jinja_context = {}

	if context.get("doctype") and context.get("docname"):
		if not frappe.has_permission(context["doctype"], "read", context["docname"]):
			frappe.throw(frappe._("You do not have permission to preview this document."), frappe.PermissionError)
		jinja_context["doc"] = frappe.get_doc(context["doctype"], context["docname"])
	elif template.reference_doctype:
		sample_name = frappe.db.get_value(
			template.reference_doctype, {}, "name", order_by="creation desc"
		)
		if sample_name:
			jinja_context["doc"] = frappe.get_doc(template.reference_doctype, sample_name)

	for key, value in context.items():
		if key not in ("doctype", "docname"):
			jinja_context[key] = value

	try:
		rendered_subject = frappe.render_template(template.subject, jinja_context) if template.subject else ""
		rendered_message = frappe.render_template(template.message, jinja_context) if template.message else ""
		# Templates no longer carry recipient fields — To/CC/BCC come from
		# the Nexlify Email Rule (override fields) or manual entry only.
		rendered_to = ""
		rendered_cc = ""
		rendered_bcc = ""
	except Exception as e:
		frappe.msgprint(
			"Could not fully render preview - no sample document was available, "
			"or a field used in the template does not exist. (" + str(e) + ")",
			indicator="orange",
		)
		rendered_subject = template.subject or ""
		rendered_message = template.message or ""
		rendered_to = ""
		rendered_cc = ""
		rendered_bcc = ""

	attachment_list = []
	for row in template.static_attachments:
		if row.attachment:
			attachment_list.append({
				"file_url": row.attachment,
				"file_name": row.attachment.split("/")[-1],
			})

	return {
		"subject": rendered_subject,
		"message": rendered_message,
		"to": rendered_to,
		"cc": rendered_cc,
		"bcc": rendered_bcc,
		"default_from": template.default_from,
		"attachments": attachment_list,
	}


@frappe.whitelist()
def send_nexlify_email(
	template_name=None,
	context=None,
	override_to=None,
	override_cc=None,
	override_bcc=None,
	override_subject=None,
	override_from=None,
	rule=None,
	rule_overrides=None,
	override_message=None,
	manual_attachments=None,
	attach_print_pdf=False,
	print_format=None,
	send_after=None,
	send_me_a_copy=False,
	read_receipt=False,
):
	"""
	Render and send an email using the given template and context.

	Args:
	    template_name (str): Name of the Nexlify Email Template
	    context (dict | str): JSON dict with optional 'doctype'/'docname' and arbitrary keys
	    override_to (str, optional): Override recipient email(s)
	    rule (str, optional): Name of the Nexlify Email Rule that triggered this send

	Returns:
	    dict: Status of the send operation
	"""
	if isinstance(context, str):
		context = json.loads(context)

	if template_name:
		template = frappe.get_doc("Nexlify Email Template", template_name)
	else:
		# No template selected — the user is composing a plain email from
		# scratch via the "Send Email" dialog. Use a stand-in object exposing
		# the same attributes as a real template, all empty, so the rest of
		# this function (which reads template.subject, template.message, etc.)
		# works completely unchanged.
		template = frappe._dict({
			"subject": "",
			"message": "",
			"default_to": "",
			"default_cc": "",
			"default_bcc": "",
			"default_from": None,
			"static_attachments": [],
		})

	# Build Jinja context
	jinja_context = {}
	if context.get("doctype") and context.get("docname"):
		if not frappe.has_permission(context["doctype"], "read", context["docname"]):
			frappe.throw(frappe._("You do not have permission to send emails for this document."), frappe.PermissionError)
		jinja_context["doc"] = frappe.get_doc(context["doctype"], context["docname"])

	for key, value in context.items():
		if key not in ("doctype", "docname"):
			jinja_context[key] = value

	# Render fields
	rendered_subject = frappe.render_template(template.subject, jinja_context) if template.subject else ""
	rendered_message = frappe.render_template(template.message, jinja_context) if template.message else ""
	# Templates no longer carry recipient fields — To/CC/BCC come from
	# the Nexlify Email Rule (rule_overrides) or manual entry (override_to/
	# override_cc/override_bcc) only.
	rendered_to = ""
	rendered_cc = ""
	rendered_bcc = ""

	# A manually-edited message (from the "Send Email" dialog) takes
	# priority over the template's own message when provided.
	if override_message:
		rendered_message = frappe.render_template(override_message, jinja_context)

	# A manually-edited subject (from the "Send Email" dialog) takes
	# priority over the template's own subject when provided.
	if override_subject:
		rendered_subject = frappe.render_template(override_subject, jinja_context)

	# Apply rule-level overrides on top of the template defaults, if provided.
	# Each override is rendered through Jinja too, so {{ doc.x }} works there as well.
	if rule_overrides:
		if rule_overrides.get("override_from"):
			template.default_from = rule_overrides["override_from"]
		if rule_overrides.get("override_to"):
			rendered_to = frappe.render_template(rule_overrides["override_to"], jinja_context)
		if rule_overrides.get("override_cc"):
			rendered_cc = frappe.render_template(rule_overrides["override_cc"], jinja_context)
		if rule_overrides.get("override_bcc"):
			rendered_bcc = frappe.render_template(rule_overrides["override_bcc"], jinja_context)
		if rule_overrides.get("override_subject"):
			rendered_subject = frappe.render_template(rule_overrides["override_subject"], jinja_context)

	# Build recipients
	recipients = override_to if override_to else _resolve_recipients(rendered_to)

	if not recipients:
		frappe.throw(frappe._("At least one recipient (To) is required to send this email."), frappe.ValidationError)
	if isinstance(recipients, str):
		recipients = [r.strip() for r in recipients.split(",") if r.strip()]

	# override_cc/override_bcc come from the manual "Send Email" dialog and
	# take priority over template/rule values, same as override_to already
	# does. An empty string means "explicitly cleared" and must result in no
	# CC/BCC, so we check for None specifically (not falsy) to distinguish
	# "field left untouched" from "field cleared by the user".
	if override_cc is not None:
		cc_list = _resolve_recipients(override_cc) or None
	else:
		cc_list = _resolve_recipients(rendered_cc) or None

	if override_bcc is not None:
		bcc_list = _resolve_recipients(override_bcc) or None
	else:
		bcc_list = _resolve_recipients(rendered_bcc) or None

	# Determine sender
	# A manually-chosen sender (from the "Send Email" dialog's Send From
	# field) takes priority over the template's own default_from.
	sender = None
	from_account = override_from or template.default_from

	if not from_account:
		frappe.throw(frappe._("A sender (From) is required to send this email."), frappe.ValidationError)
	if from_account:
		email_account = frappe.get_doc("Email Account", from_account)
		sender = email_account.email_id

	# Build attachments from static_attachments child table.
	# frappe.core.doctype.communication.email.make() expects attachments as
	# either File doc names (strings) or {"fname": ..., "fcontent": ...} dicts —
	# NOT the {"fid"/"file_url"} shape frappe.sendmail() used. We resolve each
	# static attachment to its File doc name.
	attachments = []
	for row in template.static_attachments:
		if row.attachment:
			try:
				file_data = frappe.get_doc("File", {"file_url": row.attachment})
				attachments.append(file_data.name)
			except frappe.DoesNotExistError:
				frappe.log_error(
					title="Nexlify Email Engine: Static attachment file not found",
					message=f"Template '{template_name}' references missing file: {row.attachment}",
				)

	# Manual attachments passed from the "Send Email" dialog (existing File
	# doc names, e.g. uploaded via the file picker).
	if manual_attachments:
		if isinstance(manual_attachments, str):
			manual_attachments = json.loads(manual_attachments)
		attachments.extend(manual_attachments)

	# Optionally generate and attach a Print PDF of the reference document.
	if cint(attach_print_pdf) and context.get("doctype") and context.get("docname"):
		try:
			pdf_content = frappe.get_print(
				context["doctype"],
				context["docname"],
				print_format=print_format,
				as_pdf=True,
			)
			pdf_filename = f"{context['docname']}.pdf"
			attachments.append({"fname": pdf_filename, "fcontent": pdf_content})
		except Exception as e:
			frappe.log_error(
				title=f"Nexlify Email Engine: Failed to generate Print PDF for {context.get('doctype')} {context.get('docname')}",
				message=frappe.get_traceback(),
			)

	# Send the email via frappe's Communication layer — this creates a
	# Communication doc linked to the reference document (so it shows up in
	# that document's Timeline/Comments, exactly like any normal email sent
	# from the UI), and it queues the actual send through Email Queue itself.
	log = frappe.new_doc("Nexlify Email Log")
	log.template = template_name
	log.rule = rule or ""
	log.reference_doctype = context.get("doctype", "")
	log.reference_name = context.get("docname", "")

	try:
		from frappe.core.doctype.communication.email import make

		make(
			doctype=context.get("doctype"),
			name=context.get("docname"),
			content=_wrap_html_email(rendered_message, subject=rendered_subject, sender=sender),
			subject=rendered_subject,
			sender=sender,
			recipients=", ".join(recipients) if isinstance(recipients, list) else recipients,
			cc=", ".join(cc_list) if cc_list else None,
			bcc=", ".join(bcc_list) if bcc_list else None,
			communication_medium="Email",
			send_email=True,
			attachments=attachments,
			# When send_after is set, make() queues the email for later instead
			# of sending immediately — 'now=True' only applies to immediate sends.
			now=True if not send_after else False,
			send_after=send_after,
			send_me_a_copy=cint(send_me_a_copy),
			read_receipt=cint(read_receipt),
		)
		log.status = "Success"
		# Track Last Sent / Send Count on the Rule itself (only for
		# rule-triggered sends, not manual one-off sends with no rule),
		# and auto-disable the rule once it hits its configured max_sends.
		if rule:
			try:
				rule_doc = frappe.get_doc("Nexlify Email Rule", rule)
				rule_doc.db_set("last_sent", frappe.utils.now_datetime(), update_modified=False)
				rule_doc.db_set("send_count", (rule_doc.send_count or 0) + 1, update_modified=False)
				if rule_doc.max_sends and (rule_doc.send_count or 0) >= rule_doc.max_sends:
					rule_doc.db_set("enabled", 0, update_modified=False)
			except Exception:
				frappe.log_error(
					title=f"Nexlify Email Engine: Failed to update last_sent/send_count for rule '{rule}'",
					message=frappe.get_traceback(),
				)
	except Exception as e:
		frappe.log_error(title=f"Nexlify Email Engine: Failed to send email using template '{template_name}'", message=frappe.get_traceback())
		log.status = "Failed"
		log.error_message = str(e)

	log.sent_at = frappe.utils.now_datetime()
	log.insert(ignore_permissions=True)

	return {"status": log.status, "log": log.name}


# ---------------------------------------------------------------------------
# Rule Engine — Matching, Execution, and Automatic Triggers
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_matching_rules(doctype, docname):
	"""
	Fetch all enabled manual (non-automatic) Nexlify Email Rules for a given doctype,
	evaluate their conditions, and return matching rules with their actions.

	Args:
	    doctype (str): The DocType name
	    docname (str): The document name

	Returns:
	    list[dict]: Matching rules with name, button_label, requires_extra_input,
	                extra_input_fields, and actions
	"""
	if not frappe.has_permission(doctype, "read", docname):
		frappe.throw(frappe._("You do not have permission to view rules for this document."), frappe.PermissionError)

	doc = frappe.get_doc(doctype, docname)

	rules = frappe.get_all(
		"Nexlify Email Rule",
		filters={
			"reference_doctype": doctype,
			"enabled": 1,
			"send_automatically": 0,
			"show_manual_button": 1,
		},
		order_by="priority asc",
		pluck="name",
	)

	matching = []
	for rule_name in rules:
		rule = frappe.get_doc("Nexlify Email Rule", rule_name)

		# Evaluate condition
		if not _evaluate_condition(rule, doc):
			continue

		# Build actions list
		actions = []
		for action in rule.actions:
			actions.append({
				"action_type": action.action_type,
				"email_template": action.email_template,
			})

		# Build extra input fields
		extra_fields = []
		for field in rule.extra_input_fields:
			extra_fields.append({
				"fieldname": field.fieldname,
				"label": field.label,
				"fieldtype": field.fieldtype,
				"reqd": field.reqd,
			})

		matching.append({
			"name": rule.name,
			"button_label": rule.button_label or "Send Email",
			"requires_extra_input": rule.requires_extra_input,
			"extra_input_fields": extra_fields,
			"actions": actions,
			"overrides": {
				"override_from": rule.sender,
				"override_to": rule.to,
				"override_cc": rule.cc,
				"override_bcc": rule.bcc,
				"override_subject": rule.subject,
			},
		})

	return matching


@frappe.whitelist()
def execute_rule_actions(rule_name, doctype, docname, extra_context=None):
	"""
	Execute all actions for a given rule on a document.

	For each action with action_type == "Send Email", calls the existing
	send_nexlify_email with the rule name passed for logging.

	Args:
	    rule_name (str): Name of the Nexlify Email Rule
	    doctype (str): The DocType name
	    docname (str): The document name
	    extra_context (dict | str, optional): Extra key-value pairs to merge into context

	Returns:
	    list[dict]: Status results for each action
	"""
	if isinstance(extra_context, str):
		extra_context = json.loads(extra_context) if extra_context else {}
	extra_context = extra_context or {}

	if not frappe.has_permission(doctype, "read", docname):
		frappe.throw(frappe._("You do not have permission to execute rules for this document."), frappe.PermissionError)

	rule = frappe.get_doc("Nexlify Email Rule", rule_name)
	context = {"doctype": doctype, "docname": docname}
	context.update(extra_context)

	results = []
	for action in rule.actions:
		if action.action_type == "Send Email" and action.email_template:
			result = send_nexlify_email(
				template_name=action.email_template,
				context=context,
				rule=rule_name,
				rule_overrides={
				"override_from": rule.sender,
				"override_to": rule.to,
				"override_cc": rule.cc,
				"override_bcc": rule.bcc,
				"override_subject": rule.subject,
				},
			)
			results.append({
				"action_type": action.action_type,
				"email_template": action.email_template,
				"status": result["status"],
				"log": result["log"],
			})

	return results


def check_and_run_automatic_rules(doc, method):
	"""
	Hook function called from doc_events for on_submit and on_update.
	Checks all enabled automatic rules for the document's doctype and
	executes them if their trigger conditions are met.

	This function is NOT whitelisted — it is called internally by Frappe hooks.

	Args:
	    doc: The document being saved/submitted
	    method (str): The event method name (e.g. "on_submit", "on_update")
	"""
	if frappe.flags.in_import or frappe.flags.in_patch or frappe.flags.in_install:
		return

	rules = frappe.get_all(
		"Nexlify Email Rule",
		filters={
			"reference_doctype": doc.doctype,
			"enabled": 1,
			"send_automatically": 1,
		},
		order_by="priority asc",
		pluck="name",
	)

	for rule_name in rules:
		rule = frappe.get_doc("Nexlify Email Rule", rule_name)

		# Determine if this event matches the trigger
		trigger_event = rule.trigger_event

		if trigger_event == "On Submit" and method != "on_submit":
			continue

		if trigger_event == "On Cancel" and method != "on_cancel":
			continue

		if trigger_event == "On Create":
			# Rely on Frappe's own after_insert hook, which fires exactly once
			# per document creation regardless of how it was created (normal
			# form save, API call, bulk import, etc). We previously used
			# get_doc_before_save() to detect "new" documents, but that only
			# gets populated during the standard form-save flow and returns
			# None (falsely indicating "new") for documents updated through
			# other paths, causing this trigger to misfire on real updates.
			if method != "after_insert":
				continue

		if trigger_event == "On Every Save":
			# Fires on every on_update, whether the document is new or an
			# existing one being edited. No "new vs existing" distinction and
			# deliberately NO duplicate-prevention below, since this trigger is
			# meant to fire every time by design.
			if method != "on_update":
				continue

		if trigger_event == "On Update":
			# Fires only when an EXISTING document is edited — the opposite of
			# On Create. We compare modified vs creation timestamps (reliable,
			# stored data) rather than get_doc_before_save() (in-memory only,
			# unreliable outside the standard form-save flow — see On Create
			# above for the bug this caused there).
			if method != "on_update":
				continue
			if doc.modified == doc.creation:
				# This save IS the creation itself — not an update to an
				# existing document — so skip.
				continue


		if trigger_event == "On Status Change":
			trigger_field = rule.trigger_status_field or "status"
			trigger_value = rule.trigger_status_value

			if not trigger_value:
				continue

			new_value = doc.get(trigger_field)
			if new_value != trigger_value:
				continue

			# --- DUPLICATE PREVENTION ---
			# Compare with the value BEFORE this save to ensure the status
			# actually changed to the target value, preventing re-sending
			# on repeated saves where the status stays the same.
			old_doc = doc.get_doc_before_save()
			if old_doc:
				old_value = old_doc.get(trigger_field)
				if old_value == new_value:
					# Status did not change — skip to avoid duplicate send
					continue
			else:
				# No previous doc (first save) — only trigger if the
				# initial value already matches the target
				# (unlikely for status-based triggers, but safe)
				pass

		# --- DUPLICATE PREVENTION for one-time triggers ---
		# On Submit / On Create should each only ever fire once per document.
		if trigger_event in ("On Submit", "On Create"):
			if _already_sent(rule_name, doc.doctype, doc.name):
				continue

		# Evaluate the Python condition if set
		if not _evaluate_condition(rule, doc):
			continue

		# Execute all actions for this rule
		context = {"doctype": doc.doctype, "docname": doc.name}
		for action in rule.actions:
			if action.action_type == "Send Email" and action.email_template:
				try:
					send_nexlify_email(
						template_name=action.email_template,
						context=context,
						rule=rule_name,
						rule_overrides={
				"override_from": rule.sender,
				"override_to": rule.to,
				"override_cc": rule.cc,
				"override_bcc": rule.bcc,
				"override_subject": rule.subject,
						},
					)
				except Exception as e:
					frappe.log_error(
						title=f"Nexlify Email Engine: Automatic rule '{rule_name}' action failed",
						message=frappe.get_traceback(),
					)


def _is_within_active_window(rule):
	"""
	Check whether today falls within the rule's optional Active From/To
	window. Both fields are optional — an unset field means no bound on
	that side. Returns True (no restriction) if neither field is set.
	"""
	today = frappe.utils.getdate()

	if rule.trigger_active_from and today < frappe.utils.getdate(rule.trigger_active_from):
		return False

	if rule.trigger_active_to and today > frappe.utils.getdate(rule.trigger_active_to):
		return False

	return True


def _wrap_html_email(html_content, subject=None, sender=None):
	"""
	Wrap the message using Frappe's own get_formatted_html(), the exact
	function Frappe's Compose Email / frappe.sendmail() use internally to
	build the final HTML (confirmed by inspecting a real sent Email Queue
	message: it includes a <meta viewport> and Frappe's standard email CSS,
	which renders correctly at real size on mobile with no horizontal
	scroll -- matching what our make()-based send was missing).
	"""
	if not html_content:
		return html_content

	from frappe.email.email_body import inline_style_in_html

	# Our html_content has already been Jinja-rendered (doc.x values are
	# real data, not template syntax) BEFORE this function is called, so we
	# must NOT pass it through get_formatted_html(raw_html=True), which
	# internally calls frappe.render_template() again and throws
	# UndefinedError('doc' is undefined) since no doc/jinja context is
	# available at that point (confirmed via bench console reproduction).
	# Instead we manually build the same <html><head><meta viewport> wrapper
	# Frappe's standard.html uses, then call inline_style_in_html() directly
	# -- the same final step get_formatted_html() itself calls -- to inline
	# Frappe's responsive email CSS without any extra Jinja rendering.
	title = frappe.utils.escape_html(subject or "")
	wrapped = (
		'<html><head><meta name="viewport" content="width=device-width">'
		"<title>" + title + "</title></head><body>" + html_content + "</body></html>"
	)

	try:
		return inline_style_in_html(wrapped, add_css=True)
	except Exception:
		frappe.log_error(
			title="Nexlify Email Engine: Failed to inline email CSS, sending unstyled",
			message=frappe.get_traceback(),
		)
		return wrapped


def _evaluate_condition(rule, doc):
	"""
	Evaluate a rule's condition against a document.

	Args:
	    rule: Nexlify Email Rule document
	    doc: The target document

	Returns:
	    bool: True if the condition passes (or condition_type is "Always")
	"""
	if rule.condition_type == "Always" or not rule.condition:
		return True

	if rule.condition_type == "Python Expression":
		try:
			result = frappe.safe_eval(rule.condition, {"doc": doc})
			return bool(result)
		except Exception:
			# If evaluation fails, skip the rule
			return False

	return True

@frappe.whitelist()
def get_templates_for_doctype(doctype):
	"""
	Return all enabled Nexlify Email Templates whose reference_doctype
	matches the given doctype, for use in the manual "Send Email" picker.
	"""
	if not frappe.has_permission(doctype, "read"):
		frappe.throw(frappe._("You do not have permission to send emails for this document type."), frappe.PermissionError)

	return frappe.get_all(
		"Nexlify Email Template",
		filters={"reference_doctype": doctype, "enabled": 1},
		fields=["name", "template_name", "subject"],
		order_by="template_name asc",
	)


@frappe.whitelist()
@frappe.whitelist()
def test_rule_condition(rule_name, docname):
	"""
	Test a Nexlify Email Rule's condition against a real, user-picked
	document, without actually sending anything. Returns whether the
	condition matched, and if so, a full rendered preview (from/to/cc/bcc/
	subject/message) using the rule's own overrides and its first Send
	Email action's template, exactly as a real automatic send would.
	"""
	rule = frappe.get_doc("Nexlify Email Rule", rule_name)

	if not frappe.has_permission(rule.reference_doctype, "read", docname):
		frappe.throw(frappe._("You do not have permission to read this document."), frappe.PermissionError)

	doc = frappe.get_doc(rule.reference_doctype, docname)

	if not _evaluate_condition(rule, doc):
		return {"condition_met": False}

	template_name = None
	for action in rule.actions:
		if action.action_type == "Send Email" and action.email_template:
			template_name = action.email_template
			break

	if not template_name:
		return {"condition_met": True, "error": "No Send Email action with a template configured on this rule."}

	preview = render_preview(
		template_name=template_name,
		context={"doctype": rule.reference_doctype, "docname": docname},
	)

	# Apply the rule's own overrides on top, same priority order as a real send.
	result = {
		"condition_met": True,
		"sender": rule.sender or preview.get("default_from"),
		"to": rule.to or preview.get("to"),
		"cc": rule.cc or preview.get("cc"),
		"bcc": rule.bcc or preview.get("bcc"),
		"subject": rule.subject or preview.get("subject"),
		"message": preview.get("message"),
	}
	return result


@frappe.whitelist()
def get_doctypes_with_rules():
	doctypes = frappe.get_all(
		"Nexlify Email Rule",
		filters={"enabled": 1, "send_automatically": 0},
		pluck="reference_doctype",
		distinct=True
	)
	return [dt for dt in doctypes if frappe.has_permission(dt, "read")]


def _resolve_recipients(raw_string):
	"""
	Resolve a comma-separated recipients string, supporting "role:RoleName"
	tokens that expand to all users holding that role.
	"""
	if not raw_string:
		return []
	resolved = []
	for token in raw_string.split(","):
		token = token.strip()
		if not token:
			continue
		if token.lower().startswith("role:"):
			role_name = token.split(":", 1)[1].strip()
			users = frappe.get_all(
				"Has Role",
				filters={"role": role_name, "parenttype": "User"},
				pluck="parent",
			)
			resolved.extend([u for u in users if u not in ("Administrator", "Guest")])
		else:
			resolved.append(token)
	seen = set()
	return [x for x in resolved if not (x in seen or seen.add(x))]


def _already_sent_today(rule_name, reference_doctype, reference_name):
	"""
	Check if a Daily rule already successfully sent an email for this
	document today, to avoid duplicate sends on repeated scheduler runs.
	"""
	today_start = frappe.utils.get_datetime(frappe.utils.today())
	return frappe.db.exists(
		"Nexlify Email Log",
		{
			"rule": rule_name,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"status": "Success",
			"sent_at": [">=", today_start],
		},
	)


def _already_sent(rule_name, reference_doctype, reference_name):
	"""
	Check if this rule already successfully sent an email for this document,
	regardless of when. Used for one-time triggers (On Submit, On Create)
	where a document should only ever fire the rule once.
	"""
	return frappe.db.exists(
		"Nexlify Email Log",
		{
			"rule": rule_name,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"status": "Success",
		},
	)


def run_date_based_email_rules():
	"""
	Handles "Days Before Date Field", "Days After Date Field", and "Fixed Date"
	triggers. Runs once daily (called from run_daily_email_rules).
	"""
	today = frappe.utils.getdate()

	for trigger_event in ("Days Before Date Field", "Days After Date Field", "Fixed Date"):
		rules = frappe.get_all(
			"Nexlify Email Rule",
			filters={"enabled": 1, "send_automatically": 1, "trigger_event": trigger_event},
			order_by="priority asc",
			pluck="name",
		)

		for rule_name in rules:
			rule = frappe.get_doc("Nexlify Email Rule", rule_name)

			if trigger_event == "Fixed Date":
				if not rule.trigger_fixed_date or frappe.utils.getdate(rule.trigger_fixed_date) != today:
					continue
				doc_rows = frappe.get_all(rule.reference_doctype, fields=["*"], limit_page_length=0)
			else:
				if not rule.trigger_date_field or rule.trigger_days_offset is None:
					continue
				if trigger_event == "Days Before Date Field":
					target_date = frappe.utils.add_days(today, rule.trigger_days_offset)
				else:
					target_date = frappe.utils.add_days(today, -rule.trigger_days_offset)
				doc_rows = frappe.get_all(
					rule.reference_doctype,
					filters={rule.trigger_date_field: target_date},
					fields=["*"],
					limit_page_length=0,
				)

			for row in doc_rows:
				doc_wrapper = frappe._dict(row)

				if not _evaluate_condition(rule, doc_wrapper):
					continue

				docname = row["name"]

				if _already_sent_today(rule_name, rule.reference_doctype, docname):
					continue

				context = {"doctype": rule.reference_doctype, "docname": docname}
				for action in rule.actions:
					if action.action_type == "Send Email" and action.email_template:
						try:
							send_nexlify_email(
								template_name=action.email_template,
								context=context,
								rule=rule_name,
								rule_overrides={
									"override_from": rule.sender,
									"override_to": rule.to,
									"override_cc": rule.cc,
									"override_bcc": rule.bcc,
									"override_subject": rule.subject,
								},
							)
						except Exception as e:
							frappe.log_error(
								title=f"Nexlify Email Engine: {trigger_event} rule '{rule_name}' action failed",
								message=frappe.get_traceback(),
							)


def _get_rule_now(rule):
	"""
	Return the current datetime, converted to the rule's configured timezone
	if one is set (otherwise the server's own local time, which is what
	frappe.utils.now_datetime() already returns).
	"""
	now = frappe.utils.now_datetime()
	if not rule.timezone:
		return now
	try:
		import pytz
		server_tz = pytz.timezone(frappe.utils.get_system_timezone())
		rule_tz = pytz.timezone(rule.timezone)
		localized = server_tz.localize(now)
		return localized.astimezone(rule_tz).replace(tzinfo=None)
	except Exception:
		return now


def _matches_schedule_time(rule):
	"""
	Check exclude-weekday, weekly-day, and send-at time-of-day settings for
	any periodic rule (Daily/Weekly/Monthly/Quarterly/Yearly/Custom Interval).
	Runs as part of a check performed every 5 minutes, so send_at is matched
	within a 5-minute window rather than requiring an exact match.
	"""
	now = _get_rule_now(rule)
	weekday_name = now.strftime("%A")

	# selected_weekdays is inclusive: blank means "any day is fine", but if
	# any days are selected, today must be one of them for the rule to fire.
	if rule.selected_weekdays:
		selected = [d.strip() for d in rule.selected_weekdays.split(",") if d.strip()]
		if selected and weekday_name not in selected:
			return False

	if rule.trigger_event == "Weekly" and rule.weekly_day:
		if weekday_name != rule.weekly_day:
			return False

	if rule.send_at:
		send_at_today = frappe.utils.get_datetime(f"{now.strftime('%Y-%m-%d')} {rule.send_at}")
		window_start = send_at_today
		window_end = send_at_today + frappe.utils.timedelta(minutes=5)
		if not (window_start <= now < window_end):
			return False

	return True


def run_scheduled_periodic_rules():
	"""
	Runs every 5 minutes (via the cron scheduler event). Handles Daily,
	Weekly, Monthly, Quarterly, Yearly, and Custom Interval triggers,
	applying each rule's schedule_section settings (send_at, weekly_day,
	selected_weekdays, timezone) before evaluating its condition.
	"""
	now = frappe.utils.now_datetime()

	for trigger_event in ("Daily", "Weekly", "Monthly", "Quarterly", "Yearly", "Custom Interval"):
		rules = frappe.get_all(
			"Nexlify Email Rule",
			filters={"enabled": 1, "send_automatically": 1, "trigger_event": trigger_event},
			order_by="priority asc",
			pluck="name",
		)

		for rule_name in rules:
			rule = frappe.get_doc("Nexlify Email Rule", rule_name)

			if not _is_within_active_window(rule):
				continue

			if not _matches_schedule_time(rule):
				continue

			if trigger_event == "Quarterly" and now.month not in (1, 4, 7, 10):
				continue

			if trigger_event == "Yearly" and now.month != 1:
				continue

			if trigger_event == "Custom Interval" and not _is_due_for_custom_interval(rule):
				continue

			doc_rows = frappe.get_all(rule.reference_doctype, fields=["*"], limit_page_length=0)

			for row in doc_rows:
				doc_wrapper = frappe._dict(row)

				if not _evaluate_condition(rule, doc_wrapper):
					continue

				docname = row["name"]

				if _already_sent_today(rule_name, rule.reference_doctype, docname):
					continue

				context = {"doctype": rule.reference_doctype, "docname": docname}
				for action in rule.actions:
					if action.action_type == "Send Email" and action.email_template:
						try:
							send_nexlify_email(
								template_name=action.email_template,
								context=context,
								rule=rule_name,
								rule_overrides={
									"override_from": rule.sender,
									"override_to": rule.to,
									"override_cc": rule.cc,
									"override_bcc": rule.bcc,
									"override_subject": rule.subject,
								},
							)
						except Exception as e:
							frappe.log_error(
								title=f"Nexlify Email Engine: {trigger_event} rule \'{rule_name}\' action failed",
								message=frappe.get_traceback(),
							)


def run_cron_email_rules():
	"""
	Runs every 5 minutes. Checks "Custom Cron Expression" rules and fires any
	whose cron schedule matches the current 5-minute window.
	"""
	from croniter import croniter

	rules = frappe.get_all(
		"Nexlify Email Rule",
		filters={"enabled": 1, "send_automatically": 1, "trigger_event": "Custom Cron Expression"},
		order_by="priority asc",
		pluck="name",
	)

	now = frappe.utils.now_datetime()

	for rule_name in rules:
		rule = frappe.get_doc("Nexlify Email Rule", rule_name)

		if not _is_within_active_window(rule):
			continue

		if not rule.trigger_cron_expression:
			continue

		try:
			# Check if the cron expression would have fired within the last 5 minutes
			iter = croniter(rule.trigger_cron_expression, now - frappe.utils.timedelta(minutes=5))
			next_run = iter.get_next(frappe.utils.datetime.datetime)
			if next_run > now:
				continue
		except Exception as e:
			frappe.log_error(
				title=f"Nexlify Email Engine: Invalid cron expression in rule '{rule_name}'",
				message=f"Expression: {rule.trigger_cron_expression}\n{frappe.get_traceback()}",
			)
			continue

		doc_rows = frappe.get_all(rule.reference_doctype, fields=["*"], limit_page_length=0)

		for row in doc_rows:
			doc_wrapper = frappe._dict(row)

			if not _evaluate_condition(rule, doc_wrapper):
				continue

			docname = row["name"]

			# For cron rules, dedupe within the current 5-minute window rather than
			# a full day, since cron can legitimately fire multiple times per day.
			recent_cutoff = now - frappe.utils.timedelta(minutes=5)
			already_sent = frappe.db.exists(
				"Nexlify Email Log",
				{
					"rule": rule_name,
					"reference_doctype": rule.reference_doctype,
					"reference_name": docname,
					"status": "Success",
					"sent_at": [">=", recent_cutoff],
				},
			)
			if already_sent:
				continue

			context = {"doctype": rule.reference_doctype, "docname": docname}
			for action in rule.actions:
				if action.action_type == "Send Email" and action.email_template:
					try:
						send_nexlify_email(
							template_name=action.email_template,
							context=context,
							rule=rule_name,
							rule_overrides={
								"override_from": rule.sender,
								"override_to": rule.to,
								"override_cc": rule.cc,
								"override_bcc": rule.bcc,
								"override_subject": rule.subject,
							},
						)
					except Exception as e:
						frappe.log_error(
							title=f"Nexlify Email Engine: Cron rule '{rule_name}' action failed",
							message=frappe.get_traceback(),
						)


def _is_due_for_custom_interval(rule):
	"""
	For trigger_event == "Custom Interval": check whether enough time has
	passed since this rule's last successful send to fire again, based on
	repeat_every (an integer) and repeat_unit (Day/Week/Month/Quarter/Year).
	If the rule has never sent successfully before, it is considered due.
	"""
	if not rule.repeat_every or rule.repeat_every < 1:
		return False

	last_log = frappe.get_all(
		"Nexlify Email Log",
		filters={"rule": rule.name, "status": "Success"},
		fields=["sent_at"],
		order_by="sent_at desc",
		limit=1,
	)

	if not last_log:
		return True

	last_sent = frappe.utils.getdate(last_log[0]["sent_at"])
	today = frappe.utils.getdate()
	unit = (rule.repeat_unit or "Day").lower()

	if unit == "day":
		next_due = frappe.utils.add_days(last_sent, rule.repeat_every)
	elif unit == "week":
		next_due = frappe.utils.add_days(last_sent, rule.repeat_every * 7)
	elif unit == "month":
		next_due = frappe.utils.add_months(last_sent, rule.repeat_every)
	elif unit == "quarter":
		next_due = frappe.utils.add_months(last_sent, rule.repeat_every * 3)
	elif unit == "year":
		next_due = frappe.utils.add_months(last_sent, rule.repeat_every * 12)
	else:
		return False

	return today >= next_due


def run_custom_interval_email_rules():
	"""
	Scheduled daily task (called from run_daily_email_rules). Finds all
	enabled automatic rules with trigger_event = "Custom Interval", checks
	each one's due-date via _is_due_for_custom_interval, and sends for
	matching documents — same lightweight-condition + active-window pattern
	as the other periodic triggers.
	"""
	rules = frappe.get_all(
		"Nexlify Email Rule",
		filters={"enabled": 1, "send_automatically": 1, "trigger_event": "Custom Interval"},
		order_by="priority asc",
		pluck="name",
	)

	for rule_name in rules:
		rule = frappe.get_doc("Nexlify Email Rule", rule_name)

		if not _is_within_active_window(rule):
			continue

		if not _is_due_for_custom_interval(rule):
			continue

		doc_rows = frappe.get_all(rule.reference_doctype, fields=["*"], limit_page_length=0)

		for row in doc_rows:
			doc_wrapper = frappe._dict(row)

			if not _evaluate_condition(rule, doc_wrapper):
				continue

			docname = row["name"]

			if _already_sent_today(rule_name, rule.reference_doctype, docname):
				continue

			context = {"doctype": rule.reference_doctype, "docname": docname}
			for action in rule.actions:
				if action.action_type == "Send Email" and action.email_template:
					try:
						send_nexlify_email(
							template_name=action.email_template,
							context=context,
							rule=rule_name,
							rule_overrides={
								"override_from": rule.sender,
								"override_to": rule.to,
								"override_cc": rule.cc,
								"override_bcc": rule.bcc,
								"override_subject": rule.subject,
							},
						)
					except Exception as e:
						frappe.log_error(
							title=f"Nexlify Email Engine: Custom Interval rule '{rule_name}' action failed",
							message=frappe.get_traceback(),
						)


def run_daily_email_rules():
	"""
	Scheduled daily task. Finds all enabled automatic rules with
	trigger_event = "Daily", evaluates their condition against every
	document of the reference_doctype, and sends emails for matches —
	skipping documents already emailed today for this rule. Also runs
	date-based triggers (Days Before/After, Fixed Date), since those only
	need to be checked once per day.
	"""
	run_date_based_email_rules()
	run_custom_interval_email_rules()

	rules = frappe.get_all(
		"Nexlify Email Rule",
		filters={"enabled": 1, "send_automatically": 1, "trigger_event": "Daily"},
		order_by="priority asc",
		pluck="name",
	)

	for rule_name in rules:
		rule = frappe.get_doc("Nexlify Email Rule", rule_name)

		if not _is_within_active_window(rule):
			continue

		# Fetch lightweight field dicts instead of full documents — avoids
		# loading child tables and running get_doc overhead for every single
		# record just to evaluate a simple condition. Only matching documents
		# get a full frappe.get_doc() call, inside send_nexlify_email.
		doc_rows = frappe.get_all(rule.reference_doctype, fields=["*"], limit_page_length=0)

		for row in doc_rows:
			doc_wrapper = frappe._dict(row)

			if not _evaluate_condition(rule, doc_wrapper):
				continue

			docname = row["name"]

			if _already_sent_today(rule_name, rule.reference_doctype, docname):
				continue

			context = {"doctype": rule.reference_doctype, "docname": docname}
			for action in rule.actions:
				if action.action_type == "Send Email" and action.email_template:
					try:
						send_nexlify_email(
							template_name=action.email_template,
							context=context,
							rule=rule_name,
							rule_overrides={
				"override_from": rule.sender,
				"override_to": rule.to,
				"override_cc": rule.cc,
				"override_bcc": rule.bcc,
				"override_subject": rule.subject,
							},
						)
					except Exception as e:
						frappe.log_error(
							title=f"Nexlify Email Engine: Daily rule '{rule_name}' action failed",
							message=frappe.get_traceback(),
						)
