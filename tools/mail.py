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
def host():
    return settings.get("SMTP_HOST")


def port():
    return settings.get_int("SMTP_PORT", 587)


def user():
    return settings.get("SMTP_USER")


def password():
    return settings.get("SMTP_PASS")


def sender():
    return settings.get("SMTP_FROM") or user()


def use_tls():
    return settings.get_bool("SMTP_TLS", True)


def parse_recipients(to):
    return [addr.strip() for addr in (to or "").replace(";", ",").split(",") if addr.strip()]


def _build_send_email(emit, job_id=None):
    async def send_email(to: str, subject: str, body: str) -> str:
        """Send a plain-text email to one or more recipients."""
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
        if not host() or not sender():
            return "SMTP_HOST and SMTP_FROM (or SMTP_USER) must be set to send email."

        message = EmailMessage()
        message["From"] = sender()
        message["To"] = ", ".join(recipients)
        message["Subject"] = subject or "(no subject)"
        message.set_content(body)
        try:
            with smtplib.SMTP(host(), port(), timeout=20) as server:
                if use_tls():
                    server.starttls()
                if user():
                    server.login(user(), password())
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
