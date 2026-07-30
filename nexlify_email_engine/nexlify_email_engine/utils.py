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
		rendered_to = frappe.render_template(template.default_to, jinja_context) if template.default_to else ""
		rendered_cc = frappe.render_template(template.default_cc, jinja_context) if template.default_cc else ""
		rendered_bcc = frappe.render_template(template.default_bcc, jinja_context) if template.default_bcc else ""
	except Exception as e:
		frappe.msgprint(
			"Could not fully render preview - no sample document was available, "
			"or a field used in the template does not exist. (" + str(e) + ")",
			indicator="orange",
		)
		rendered_subject = template.subject or ""
		rendered_message = template.message or ""
		rendered_to = template.default_to or ""
		rendered_cc = template.default_cc or ""
		rendered_bcc = template.default_bcc or ""

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
def send_nexlify_email(template_name, context, override_to=None, rule=None):
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

	template = frappe.get_doc("Nexlify Email Template", template_name)

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
	rendered_to = frappe.render_template(template.default_to, jinja_context) if template.default_to else ""
	rendered_cc = frappe.render_template(template.default_cc, jinja_context) if template.default_cc else ""
	rendered_bcc = frappe.render_template(template.default_bcc, jinja_context) if template.default_bcc else ""

	# Build recipients
	recipients = override_to if override_to else _resolve_recipients(rendered_to)
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
	log.rule = rule or ""
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

		if trigger_event == "On Save (New)":
			# Trigger on the very first save, whether that happens via on_update
			# (normal flow) or on_submit (doctypes that auto-submit without a
			# prior on_update event ever firing).
			is_new_save = not doc.get_doc_before_save()
			if method in ("on_update", "on_submit") and is_new_save:
				pass
			else:
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
		# On Submit / On Save (New) should each only ever fire once per document.
		if trigger_event in ("On Submit", "On Save (New)"):
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
					)
				except Exception as e:
					frappe.log_error(
						title=f"Nexlify Email Engine: Automatic rule '{rule_name}' action failed",
						message=frappe.get_traceback(),
					)


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
	regardless of when. Used for one-time triggers (On Submit, On Save (New))
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


def run_daily_email_rules():
	"""
	Scheduled daily task. Finds all enabled automatic rules with
	trigger_event = "Daily", evaluates their condition against every
	document of the reference_doctype, and sends emails for matches —
	skipping documents already emailed today for this rule.
	"""
	rules = frappe.get_all(
		"Nexlify Email Rule",
		filters={"enabled": 1, "send_automatically": 1, "trigger_event": "Daily"},
		order_by="priority asc",
		pluck="name",
	)

	for rule_name in rules:
		rule = frappe.get_doc("Nexlify Email Rule", rule_name)

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
						)
					except Exception as e:
						frappe.log_error(
							title=f"Nexlify Email Engine: Daily rule '{rule_name}' action failed",
							message=frappe.get_traceback(),
						)
