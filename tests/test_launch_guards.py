"""Launch guards for the agency-managed pilot.

Run: .\\venv\\Scripts\\python.exe tests\\test_launch_guards.py

Three things a first paying client depends on: a suspended account really stops, the
session cookie is not script-readable, and a lead produces an actual client notification.
The notification is tested with a fake mail transport, so no external service is called.
"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="launch-guards-"), "test.db").replace("\\", "/")
os.environ.pop("ALLOW_LIVE_ACTIONS", None)
os.environ.pop("JOB_BACKEND", None)
os.environ.pop("REDIS_URL", None)
os.environ["NOTIFICATIONS_ENABLED"] = "1"

import app as appmod  # noqa: E402
import contacts  # noqa: E402
import db  # noqa: E402
import notifications  # noqa: E402
import orgs as orgs_service  # noqa: E402
import platform_mail  # noqa: E402

db.init_db()

FAILED = []
ORG = {
    "name": "Launch Guard Client",
    "slug": "launch-guard-client",
    "email": "owner@launch-guard.test",
    "password": "launch-pass-1234",
}
FORM = {
    "id": 1,
    "name": "Spring enquiry",
    "questions": [
        {"id": "name", "label": "Your name", "type": "text"},
        {"id": "email", "label": "Email", "type": "email"},
    ],
}


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


def login(client, email=ORG["email"], password=ORG["password"]):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_session_and_headers(org):
    client = appmod.app.test_client()
    signed_in = login(client)
    check("the client can sign in", signed_in.status_code, 200)
    check("the session cookie is HttpOnly",
          appmod.app.config["SESSION_COOKIE_HTTPONLY"], True)
    check("the session cookie is SameSite", appmod.app.config["SESSION_COOKIE_SAMESITE"], "Lax")
    check("the session has a lifetime",
          appmod.app.config["PERMANENT_SESSION_LIFETIME"].total_seconds() > 0, True)

    page = client.get("/admin/")
    check("the console answers", page.status_code, 200)
    check("the console cannot be framed", page.headers.get("X-Frame-Options"), "SAMEORIGIN")
    api = client.get("/api/profile")
    check("API responses are not cached", api.headers.get("Cache-Control"), "no-store")
    check("the profile is reachable while active", api.status_code, 200)


def test_suspension_stops_access(org):
    client = appmod.app.test_client()
    check("the client can sign in before suspension", login(client).status_code, 200)
    owner = appmod.app.test_client()
    check("the operator can sign in",
          login(owner, "admin@example.com", "admin123").status_code, 200)
    suspended = owner.put("/api/orgs/%d" % org["id"], json={"status": "suspended"})
    check("the operator can suspend the client", suspended.status_code, 200)
    blocked = client.get("/api/profile")
    check("the existing session is stopped", blocked.status_code, 403)
    check("and the answer explains why",
          "paused" in (blocked.get_json() or {}).get("error", "").lower(), True)
    check("the client cannot sign back in", login(client).status_code, 403)
    check("the operator still has the platform",
          owner.get("/api/orgs").status_code, 200)

    restored = owner.put("/api/orgs/%d" % org["id"], json={"status": "active"})
    check("the operator can reactivate the client", restored.status_code, 200)
    check("the client can sign in again", login(client).status_code, 200)


def test_ops_scripts():
    backup = open(os.path.join(ROOT, "deploy", "backup.sh"), encoding="utf-8").read()
    restore = open(os.path.join(ROOT, "deploy", "restore.sh"), encoding="utf-8").read()
    check("the backup dumps PostgreSQL", "pg_dump" in backup, True)
    check("it archives media and sites", "artifacts" in backup and "sites" in backup, True)
    check("it encrypts the archive", "openssl enc" in backup and "BACKUP_PASSPHRASE" in backup,
          True)
    check("restore needs an explicit confirmation", "RESTORE_CONFIRM" in restore, True)
    check("restore uses pg_restore", "pg_restore" in restore, True)


def test_lead_notification(org):
    contact = contacts.create_contact(
        {"name": "Ada <script>", "email": "ada@example.test", "phone": "123"},
        org["id"], source="form")
    subject, text, html_body = notifications.lead_message(
        org["id"], FORM, contact_id=contact["id"], response_id=42,
        answers={"name": "Ada <script>", "email": "ada@example.test"})
    check("the notification names the form", subject, "New lead from Spring enquiry")
    check("the text includes the lead email", "ada@example.test" in text, True)
    check("the HTML escapes user input", "&lt;script&gt;" in html_body, True)
    check("the client admins are recipients", notifications.recipients(org["id"]),
          [ORG["email"]])

    sent = []
    original = platform_mail.send
    original_scope = platform_mail.client_configured
    original_config = platform_mail.is_configured
    try:
        platform_mail.client_configured = lambda org_id=None: False
        platform_mail.is_configured = lambda org_id=None: True
        platform_mail.send = lambda to, subject, html, **kwargs: sent.append(
            {"to": to, "subject": subject, "html": html}) or {"ok": True}
        result = notifications.notify_lead(
            org["id"], FORM, contact_id=contact["id"], response_id=42,
            answers={"name": "Ada", "email": "ada@example.test"})
    finally:
        platform_mail.send = original
        platform_mail.client_configured = original_scope
        platform_mail.is_configured = original_config

    check("the lead notification reports success", result.get("ok"), True)
    check("it goes to the client admin", sent[0]["to"] if sent else None, ORG["email"])
    check("and it is a new-lead message",
          "New lead" in (sent[0]["subject"] if sent else ""), True)


def test_automation_notify(org):
    import asyncio

    import automations

    calls = []
    original = notifications.notify_org
    try:
        notifications.notify_org = lambda org_id, subject, message: calls.append(
            (org_id, subject, message)) or {"ok": True, "sent": [ORG["email"]]}
        result = asyncio.run(automations._execute(
            {"id": "report", "type": "notify", "title": "Report",
             "config": {"message": "The weekly review is done."}},
            {"name": "Weekly review", "org_id": org["id"]}, None))
    finally:
        notifications.notify_org = original
    check("an automation notify step actually sends", "Notified" in result, True)
    check("it uses the client's org", calls[0][0] if calls else None, org["id"])


def main():
    org = orgs_service.create_org(ORG)
    try:
        test_session_and_headers(org)
        test_suspension_stops_access(org)
        test_ops_scripts()
        test_lead_notification(org)
        test_automation_notify(org)
    finally:
        with db.get_connection() as conn:
            conn.execute("DELETE FROM users WHERE org_id = ?", (org["id"],))
            conn.execute("DELETE FROM contacts WHERE org_id = ?", (org["id"],))
            conn.execute("DELETE FROM business_profiles WHERE org_id = ?", (org["id"],))
            conn.execute("DELETE FROM orgs WHERE id = ?", (org["id"],))
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nLAUNCH GUARDS OK - suspension, cookies and lead notifications are wired")


if __name__ == "__main__":
    main()
