"""Client email through that client's own GoHighLevel sub-account.

Run: .\\venv\\Scripts\\python.exe tests\\test_client_ghl.py

One token per client, not one for the platform: the operator\'s account must never send
a client\'s mail, and a client without GHL settings must stay on SMTP.
"""

import asyncio
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="client-ghl-"), "test.db").replace("\\", "/")
os.environ.pop("ALLOW_LIVE_ACTIONS", None)
os.environ.pop("JOB_BACKEND", None)
os.environ.pop("REDIS_URL", None)
os.environ["GHL_API_TOKEN"] = "platform-token"
os.environ["GHL_LOCATION_ID"] = "platform-loc"

import actions  # noqa: E402
import db  # noqa: E402
import jobs  # noqa: E402
import orgs as orgs_service  # noqa: E402
import platform_mail  # noqa: E402
import settings  # noqa: E402
import tools.mail as mail_tool  # noqa: E402

db.init_db()
FAILED = []


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


def user_id():
    with db.get_connection() as conn:
        row = conn.execute("SELECT id FROM users WHERE email = ?",
                           ("admin@example.com",)).fetchone()
    return row["id"] if row else None


class Transport:
    def __init__(self):
        self.calls = []

    def __call__(self, method, url, headers, payload=None, timeout=20):
        self.calls.append({"method": method, "url": url, "headers": headers,
                           "payload": payload})
        if url.endswith("/contacts/upsert"):
            return 200, "{\"contact\": {\"id\": \"contact-1\"}}"
        if "customFields" in url:
            return 200, "{\"customFields\": []}"
        if "/locations/" in url:
            return 200, "{\"location\": {\"name\": \"Acme Sub-account\"}}"
        return 200, "{\"messageId\": \"message-1\"}"


class live_actions:
    def __enter__(self):
        self.original = (actions.is_dry_run, actions.gate)
        actions.is_dry_run = lambda: False
        actions.gate = lambda *a, **kw: {"allowed": True, "dry_run": False,
                                         "reason": "approved", "approval_id": 1}

    def __exit__(self, *exc):
        actions.is_dry_run, actions.gate = self.original


def test_the_client_has_its_own_connection():
    check("the client connection is theirs",
          (platform_mail.connection(ORG)["token"],
           platform_mail.connection(ORG)["location"]),
          ("client-token", "client-loc"))
    check("another client gets the platform connection",
          (platform_mail.connection(OTHER)["token"],
           platform_mail.connection(OTHER)["location"]),
          ("platform-token", "platform-loc"))
    check("configured for the client with credentials",
          platform_mail.is_configured(ORG), True)
    check("not configured for a client without them",
          platform_mail.is_configured(OTHER), False)
    check("the platform stays configured", platform_mail.is_configured(), True)


def test_a_send_uses_the_clients_location_and_address():
    transport = Transport()
    original = platform_mail._transport
    platform_mail._transport = transport
    try:
        result = platform_mail.send("lead@example.com", "Hello", "<p>Hello</p>",
                                    org_id=ORG)
    finally:
        platform_mail._transport = original
    check("it reports success", result["ok"], True)
    upsert = [c for c in transport.calls if c["url"].endswith("/contacts/upsert")][0]
    message = [c for c in transport.calls if c["url"].endswith("/conversations/messages")][0]
    check("the contact is created in the client location",
          upsert["payload"]["locationId"], "client-loc")
    check("the mail is sent from their address",
          "hello@client.test" in message["payload"]["emailFrom"], True)
    check("with their token", message["headers"]["Authorization"],
          "Bearer client-token")

    original = platform_mail._transport
    platform_mail._transport = Transport()
    try:
        platform_mail.send("lead@example.com", "Hello", "<p>Hello</p>", org_id=OTHER)
    except platform_mail.MailError as exc:
        check("a client without credentials is refused",
              "not configured" in str(exc) and "Client email" in str(exc), True)
    else:
        check("a client without credentials is refused", False, True)
    finally:
        platform_mail._transport = original


def test_the_test_button_uses_the_client():
    original = platform_mail._transport
    platform_mail._transport = Transport()
    try:
        good = settings.test("client_email_ghl", ORG)
        missing = settings.test("client_email_ghl", OTHER)
    finally:
        platform_mail._transport = original
    check("the test finds the sub-account", (good["ok"], "Acme" in good["output"]),
          (True, True))
    check("a client without settings is told what is missing", missing["ok"], False)


def test_send_email_prefers_ghl_and_falls_back_to_smtp():
    sent = []
    original_send = platform_mail.send
    platform_mail.send = lambda to, subject, html, org_id=None: sent.append(
        {"to": to, "org_id": org_id}) or {"ok": True}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            self.host = host

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            pass

        def login(self, user, password):
            pass

        def send_message(self, message):
            sent.append({"smtp": message["To"]})

    original_smtp = mail_tool.smtplib.SMTP
    original_host, original_sender, original_tls = (mail_tool.host, mail_tool.sender,
                                                    mail_tool.use_tls)
    mail_tool.smtplib.SMTP = FakeSMTP
    mail_tool.host = lambda org=None: "smtp.test"
    mail_tool.sender = lambda org=None: "ops@example.com"
    mail_tool.use_tls = lambda org=None: False
    try:
        with live_actions():
            ghl = asyncio.run(mail_tool._build_send_email(
                None, job_id=new_job(ORG))("lead@example.com", "Hi", "Hello"))
            smtp = asyncio.run(mail_tool._build_send_email(
                None, job_id=new_job(OTHER))("lead@example.com", "Hi", "Hello"))
    finally:
        platform_mail.send = original_send
        mail_tool.smtplib.SMTP = original_smtp
        mail_tool.host, mail_tool.sender, mail_tool.use_tls = (original_host,
                                                               original_sender,
                                                               original_tls)
    check("a GHL client is sent through GHL", "GoHighLevel" in ghl, True)
    check("and the call names that client",
          [row.get("org_id") for row in sent if "org_id" in row], [ORG])
    check("a client without GHL uses SMTP", "Sent to" in smtp and "GoHighLevel" not in smtp,
          True)
    check("the SMTP path sent one message",
          [row for row in sent if "smtp" in row], [{"smtp": "lead@example.com"}])


def new_job(org):
    return jobs.create_job("Send", "send an email", user_id(), "selector", "chat",
                           org_id=org)


if __name__ == "__main__":
    ORG = orgs_service.create_org({"name": "GHL Client"})["id"]
    OTHER = orgs_service.create_org({"name": "SMTP Client"})["id"]
    settings.set_value("GHL_CLIENT_API_TOKEN", "client-token", actor="test", org_id=ORG)
    settings.set_value("GHL_CLIENT_LOCATION_ID", "client-loc", actor="test", org_id=ORG)
    settings.set_value("GHL_CLIENT_FROM_EMAIL", "hello@client.test", actor="test", org_id=ORG)
    settings.set_value("GHL_CLIENT_FROM_NAME", "Client Brand", actor="test", org_id=ORG)
    settings.forget()
    test_the_client_has_its_own_connection()
    test_a_send_uses_the_clients_location_and_address()
    test_the_test_button_uses_the_client()
    test_send_email_prefers_ghl_and_falls_back_to_smtp()
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nCLIENT GHL OK - each client sends from their own sub-account, SMTP is the fallback")
