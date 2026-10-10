"""The Connections tab of the Email Engine documents (hooks: override_doctype_dashboards)."""

import frappe


def _add(data, label, links, near=None):
    """Connections: add links {doctype: its field that points here; a child-table field is found by Frappe}
    into the group holding `near`, else the group named label (made if missing). Only adds, never removes."""
    data.setdefault("transactions", [])
    data.setdefault("non_standard_fieldnames", {})
    shown = {d for g in data["transactions"] for d in g.get("items", [])}
    new = {dt: f for dt, f in links.items() if dt not in shown}
    if not new:
        return data
    group = next((g for g in data["transactions"] if near and near in g.get("items", [])), None) or next(
        (g for g in data["transactions"] if g.get("label") in (label, frappe._(label))), None)
    if not group:
        group = {"label": frappe._(label), "items": []}
        data["transactions"].append(group)
    for doctype, fieldname in new.items():
        group["items"].append(doctype)
        if not data.get("fieldname"):
            data["fieldname"] = fieldname
        elif fieldname != data["fieldname"]:
            data["non_standard_fieldnames"][doctype] = fieldname
    return data


def nexlify_email_rule_connections(data):
    """Nexlify Email Rule"""
    return _add(data, "Email", {'Nexlify Email Log': 'rule'})


def nexlify_email_template_connections(data):
    """Nexlify Email Template"""
    return _add(data, "Email", {'Nexlify Email Log': 'template', 'Nexlify Email Rule': 'email_template'})
