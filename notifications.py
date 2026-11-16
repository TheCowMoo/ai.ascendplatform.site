"""Operational email to a client about their own work.

The agency runs the platform, but the *client* owns the lead and the automation. A form
submission that only appears in a database is not a useful lead, so this module sends a
short notice to the client's own admins. It is deliberately best-effort: a mail outage
must never fail a form submission or an automation step.
"""

import html
import logging
import threading

import contacts as contacts_service
import platform_mail
import settings
from db import get_connection

LOGGER = logging.getLogger(__name__)


def recipients(org_id):
    """The client admins who should hear about work in their workspace.

    Client admins are the people who bought the service. If a client has only members,
    they are included rather than silently dropping the notice.
    """
    if org_id is None:
        return []
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT email FROM users WHERE org_id = ? AND role = 'client_admin' "
            "AND email <> '' ORDER BY id", (org_id,)
        ).fetchall()
        if not rows:
            rows = conn.execute(
                "SELECT email FROM users WHERE org_id = ? AND email <> '' ORDER BY id",
                (org_id,)
            ).fetchall()
    seen = set()
    out = []
    for row in rows:
        email = str(row["email"] or "").strip().lower()
        if email and email not in seen:
            seen.add(email)
            out.append(email)
    return out


def _scope_for(org_id):
    """The GoHighLevel scope to send through: the client's if they have one, else ours."""
    if org_id is not None and platform_mail.client_configured(org_id):
        return org_id
    return None


def enabled():
    """Whether the operator has left client notifications on."""
    return settings.get_bool("NOTIFICATIONS_ENABLED", True)


def send_to_client(org_id, subject, text, html_body=None):
    """Send one best-effort message to a client's admins. Never raises to the caller."""
    if not enabled():
        return {"ok": False, "error": "Client notifications are switched off."}
    targets = recipients(org_id)
    if not targets:
        return {"ok": False, "error": "This client has no login email to notify."}
    scope = _scope_for(org_id)
    if not platform_mail.is_configured(scope):
        return {"ok": False, "error": "Client email is not configured."}
    body = html_body or ("<p>%s</p>"
                         % html.escape(str(text or "")).replace("\n", "<br>"))
    sent = []
    failed = []
    for address in targets:
        try:
            platform_mail.send(address, subject, body, org_id=scope)
            sent.append(address)
        except Exception as exc:          # a mail outage must not break the work
            failed.append("%s: %s" % (address, exc))
            LOGGER.warning("notification to %s failed: %s", address, exc)
    return {"ok": bool(sent), "sent": sent, "failed": failed,
            "error": "" if sent else (failed[0] if failed else "No message sent.")}


def _link(path):
    base = (settings.get("PLATFORM_URL") or "").strip().rstrip("/")
    return (base + path) if base else ""

def _answers_html(form, answers):
    questions = form.get("questions") or []
    rows = []
    for question in questions:
        if not isinstance(question, dict):
            continue
        value = (answers or {}).get(question.get("id"))
        if value in (None, "", []):
            continue
        if isinstance(value, list):
            value = ", ".join(str(item) for item in value)
        rows.append("<tr><td>%s</td><td>%s</td></tr>" % (
            html.escape(str(question.get("label") or question.get("id") or "")),
            html.escape(str(value)),
        ))
    return "".join(rows)


def lead_message(org_id, form, contact_id=None, response_id=None, answers=None):
    """The subject/text/html for a new lead. Pure enough to test without a mail server."""
    contact = (contacts_service.get_contact(contact_id, org_id)
               if contact_id else None) or {}
    form_name = str(form.get("name") or "your form")
    subject = "New lead from %s" % form_name
    name = str(contact.get("name") or "New lead")
    email = str(contact.get("email") or "")
    phone = str(contact.get("phone") or "")
    lines = [
        "New form submission",
        "Form: %s" % form_name,
        "Name: %s" % name,
        "Email: %s" % (email or "(not supplied)"),
        "Phone: %s" % (phone or "(not supplied)"),
    ]
    contact_link = _link("/admin/#/app/contacts")
    forms_link = _link("/admin/#/app/forms")
    if contact_link:
        lines.append("Contacts: %s" % contact_link)
    if forms_link:
        lines.append("Responses: %s" % forms_link)
    if response_id:
        lines.append("Response: #%s" % response_id)
    text = "\n".join(lines)
    rows = _answers_html(form, answers)
    details = ""
    if rows:
        details = ("<h3>Answers</h3><table cellpadding=\"6\" cellspacing=\"0\" "
                   "border=\"0\" style=\"border-collapse:collapse\">%s</table>" % rows)
    links = []
    if contact_link:
        links.append('<a href="%s">Open contacts</a>' % html.escape(contact_link, quote=True))
    if forms_link:
        links.append('<a href="%s">Open responses</a>' % html.escape(forms_link, quote=True))
    html_body = (
        "<p><strong>New form submission</strong></p>"
        "<p><strong>Form:</strong> %s<br><strong>Name:</strong> %s<br>"
        "<strong>Email:</strong> %s<br><strong>Phone:</strong> %s</p>%s"
        "<p>%s</p>"
        % (html.escape(form_name), html.escape(name), html.escape(email or "(not supplied)"),
           html.escape(phone or "(not supplied)"), details, " · ".join(links))
    )
    return subject, text, html_body


def notify_lead(org_id, form, contact_id=None, response_id=None, answers=None):
    """Send a new-lead notice synchronously. Returns the delivery result."""
    subject, text, html_body = lead_message(org_id, form, contact_id, response_id, answers)
    return send_to_client(org_id, subject, text, html_body)


def notify_lead_async(org_id, form, contact_id=None, response_id=None, answers=None):
    """Queue a lead notice with a daemon thread so a public form stays fast."""
    thread = threading.Thread(
        target=notify_lead,
        args=(org_id, form, contact_id, response_id, answers),
        name="lead-notification",
        daemon=True,
    )
    thread.start()
    return thread


def notify_org(org_id, subject, message):
    """Send an operator/automation note to a client's admins."""
    text = str(message or "").strip()
    if not text:
        return {"ok": False, "error": "There is no message to send."}
    return send_to_client(org_id, str(subject or "Update from your team"), text)
