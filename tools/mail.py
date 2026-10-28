"""
Email tool (stage S5).

Uses the standard library's ``smtplib``, matching the ``urllib``-only HTTP already
used in ``meta.py`` — no extra dependency. In dry-run nothing is sent.
"""

import smtplib
from email.message import EmailMessage

import actions
import settings
from tools import register


# Every mail setting is read per call, so a change in the Settings tab applies to
# the next send without restarting the app.
#
# ``org_id`` selects the client's own SMTP where they have configured one, falling
# back to the platform's: these fields are client-scoped, so a client sends as
# themselves rather than through the operator's mailbox.
def org_of(job_id):
    """The client whose mail configuration a job should send through, or None.

    The job is the only thing that knows which client asked for the send. Imported
    lazily because jobs.py pulls in the tool registry, which imports this module.
    """
    if not job_id:
        return None
    try:
        import jobs
        job = jobs.get_job(job_id)
    except Exception:
        return None
    return (job or {}).get("org_id")


def host(org_id=None):
    return settings.get("SMTP_HOST", org_id=org_id)


def port(org_id=None):
    return settings.get_int("SMTP_PORT", 587, org_id=org_id)


def user(org_id=None):
    return settings.get("SMTP_USER", org_id=org_id)


def password(org_id=None):
    return settings.get("SMTP_PASS", org_id=org_id)


def sender(org_id=None):
    return settings.get("SMTP_FROM", org_id=org_id) or user(org_id)


def use_tls(org_id=None):
    return settings.get_bool("SMTP_TLS", True, org_id=org_id)


def parse_recipients(to):
    return [addr.strip() for addr in (to or "").replace(";", ",").split(",") if addr.strip()]


def _build_send_email(emit, job_id=None):
    async def send_email(to: str = "", subject: str = "", body: str = "",
                        template_id: int = 0, contact_id: int = 0) -> str:
        """Send a plain-text email, optionally rendered from a saved template.

        A sequence points at a template and a contact rather than carrying the wording:
        ``template_id`` + ``contact_id`` fills in {{contact.email}}-style placeholders, and
        ``to`` defaults to the contact's address when it is left empty.
        """
        org = org_of(job_id)
        if template_id:
            import contacts as contacts_service
            import templates_service

            template = templates_service.get_template(int(template_id), org or 0) if org else None
            if not template:
                return "No template %s for this client." % template_id
            contact = (contacts_service.get_contact(int(contact_id), org)
                       if (org and contact_id) else None)
            if contact_id and not contact:
                return ("No contact %s for this client, so the template has no values to "
                        "fill in." % contact_id)
            subject = subject.strip() or templates_service.render(template["subject"], contact)
            body = body.strip() or templates_service.render(template["body"], contact)
            if not (to or "").strip() and contact:
                to = contact["email"]
        recipients = parse_recipients(to)
        if not recipients:
            return "No recipient given."
        if not (body or "").strip():
            return "The email body is empty."

        authorisation = actions.gate(
            "email",
            "send an email to %s" % ", ".join(recipients),
            {"to": recipients, "subject": subject},
            job_id,
            emit,
        )
        if not authorisation["allowed"]:
            return "Not sent: %s" % authorisation["reason"]
        if authorisation["dry_run"]:
            return "[dry-run] would email %s (subject: %s)" % (", ".join(recipients), subject)
        if not host(org) or not sender(org):
            return "SMTP_HOST and SMTP_FROM (or SMTP_USER) must be set to send email."

        message = EmailMessage()
        message["From"] = sender(org)
        message["To"] = ", ".join(recipients)
        message["Subject"] = subject or "(no subject)"
        message.set_content(body)
        try:
            with smtplib.SMTP(host(org), port(org), timeout=20) as server:
                if use_tls(org):
                    server.starttls()
                if user(org):
                    server.login(user(org), password(org))
                server.send_message(message)
        except Exception as exc:
            return "Sending failed (%s: %s)" % (type(exc).__name__, exc)
        return "Sent to %s" % ", ".join(recipients)

    return send_email


register(
    "send_email",
    "Send a plain-text email to one or more recipients.",
    _build_send_email,
)
