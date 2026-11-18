r"""Two-way contacts, entirely offline: the mirror's merge rules are the product.

Run: .\venv\Scripts\python.exe tests\test_contact_sync.py
"""

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="contact-sync-"), "test.db").replace(os.sep, "/")
os.environ.pop("ALLOW_LIVE_ACTIONS", None)
os.environ.pop("JOB_BACKEND", None)
os.environ.pop("REDIS_URL", None)

import app as appmod  # noqa: E402
import contact_sync  # noqa: E402
import contacts  # noqa: E402
import db  # noqa: E402
import forms  # noqa: E402
import jobs  # noqa: E402
import orgs as orgs_service  # noqa: E402
import platform_mail  # noqa: E402
import settings  # noqa: E402

CATALOGUE = [
    {"id": "cf-cid", "name": "AscendPlatform contact id",
     "fieldKey": "contact.ascend_contact_id", "dataType": "TEXT"},
    {"id": "cf-status", "name": "AscendPlatform status",
     "fieldKey": "contact.ascend_status", "dataType": "TEXT"},
    {"id": "cf-source", "name": "AscendPlatform source",
     "fieldKey": "contact.ascend_source", "dataType": "TEXT"},
    {"id": "cf-notes", "name": "AscendPlatform notes",
     "fieldKey": "contact.ascend_notes", "dataType": "LARGE_TEXT"},
    {"id": "cf-activity", "name": "AscendPlatform activity",
     "fieldKey": "contact.ascend_activity", "dataType": "LARGE_TEXT"},
    {"id": "cf-created", "name": "AscendPlatform created",
     "fieldKey": "contact.ascend_created_at", "dataType": "TEXT"},
    {"id": "cf-updated", "name": "AscendPlatform updated",
     "fieldKey": "contact.ascend_updated_at", "dataType": "TEXT"},
]

FAILED = []


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


class Transport:
    def __init__(self, remote=None):
        self.remote = list(remote or [])
        self.calls = []
        self.sequence = 0

    def __call__(self, method, url, headers, payload=None, timeout=20):
        self.calls.append({"method": method, "url": url, "payload": payload})
        if "/customFields" in url:
            return 200, json.dumps({"customFields": CATALOGUE})
        if "/contacts/upsert" in url or (
                method == "PUT" and "/contacts/" in url):
            self.sequence += 1
            remote_id = "remote-%d" % self.sequence
            if method == "PUT":
                remote_id = url.rsplit("/", 1)[-1]
            return 200, json.dumps({"contact": {
                "id": remote_id,
                "dateUpdated": "2099-01-01T00:00:%02dZ" % (self.sequence % 60),
            }})
        if "/contacts/?" in url or url.endswith("/contacts/"):
            return 200, json.dumps({"contacts": self.remote, "meta": {}})
        return 200, json.dumps({})


def configured(org):
    settings.set_value("GHL_CLIENT_API_TOKEN", "test-token", actor="test", org_id=org)
    settings.set_value("GHL_CLIENT_LOCATION_ID", "test-location", actor="test", org_id=org)
    settings.forget()


def new_org(name):
    org = orgs_service.create_org({"name": name})["id"]
    configured(org)
    return org


def sync(transport, org):
    original = platform_mail._transport
    platform_mail._transport = transport
    try:
        return contact_sync.sync_org(org)
    finally:
        platform_mail._transport = original


def test_a_local_contact_is_created_remotely():
    org = new_org("Mirror Push")
    item = contacts.create_contact({"name": "Ada Lovelace", "email": "ada@example.test",
                                    "phone": "+1 555 0100", "tags": ["vip"],
                                    "notes": "Prefers email"}, org, source="form")
    transport = Transport()
    result = sync(transport, org)
    saved = contacts.get_contact(item["id"], org)
    check("the local contact is pushed", result["pushed"], 1)
    check("the remote id is stored", bool(saved["ghl_contact_id"]), True)
    check("a snapshot is stored", bool(saved["ghl_snapshot"]), True)
    check("the source survives", saved["source"], "form")
    upsert = [call for call in transport.calls if "/contacts/upsert" in call["url"]][0]
    check("the remote source is branded", upsert["payload"]["source"], "AscendPlatform")
    check("the name travels", upsert["payload"]["name"], "Ada Lovelace")
    check("the tags travel", upsert["payload"]["tags"], ["vip"])
    field_values = {field["id"]: field["value"] for field in upsert["payload"]["customFields"]}
    check("the status is mirrored", field_values.get("cf-status"), "new")
    check("the notes are mirrored", field_values.get("cf-notes"), "Prefers email")


def test_a_remote_contact_is_imported_with_all_fields():
    org = new_org("Mirror Pull")
    remote = {
        "id": "remote-1", "name": "Grace Hopper", "email": "grace@example.test",
        "phone": "+1 555 0101", "tags": ["navy"], "dateUpdated": "2026-01-01T00:00:00Z",
        "customFields": [
            {"id": "cf-status", "value": "customer"},
            {"id": "cf-source", "value": "webhook"},
            {"id": "cf-notes", "value": "Imported note"},
        ],
    }
    result = sync(Transport([remote]), org)
    rows = contacts.list_all_contacts(org)
    check("the remote contact is pulled", result["pulled"], 1)
    check("one local row exists", len(rows), 1)
    row = rows[0]
    check("the name is imported", row["name"], "Grace Hopper")
    check("the status is imported", row["status"], "customer")
    check("the notes are imported", row["notes"], "Imported note")
    check("the tags are imported", row["tags"], ["navy"])
    check("the imported source is marked", row["source"], "ghl")
    check("the remote id is retained", row["ghl_contact_id"], "remote-1")


def test_remote_changes_pull_and_local_changes_push():
    org = new_org("Mirror Merge")
    item = contacts.create_contact({"name": "Katherine Johnson", "email": "kj@example.test"},
                                    org, source="manual")
    transport = Transport()
    sync(transport, org)
    remote_id = contacts.get_contact(item["id"], org)["ghl_contact_id"]
    transport.remote = [{"id": remote_id, "name": "Katherine G. Johnson",
                         "email": "kj@example.test", "tags": ["math"],
                         "dateUpdated": "2099-02-01T00:00:00Z",
                         "customFields": [{"id": "cf-status", "value": "qualified"}]}]
    pulled = sync(transport, org)
    row = contacts.get_contact(item["id"], org)
    check("a remote-only change is pulled", row["name"], "Katherine G. Johnson")
    check("the remote status is pulled", row["status"], "qualified")
    check("the pull is counted", pulled["pulled"], 1)

    contacts.update_contact(item["id"], org, {"notes": "Call after lunch"})
    transport.remote[0]["name"] = "Katherine G. Johnson"
    pushed = sync(transport, org)
    row = contacts.get_contact(item["id"], org)
    check("a local-only change is pushed", row["notes"], "Call after lunch")
    check("the push is counted", pushed["pushed"], 1)


def test_a_simultaneous_change_is_reported_and_newest_wins():
    org = new_org("Mirror Conflict")
    item = contacts.create_contact({"name": "Local Name", "email": "both@example.test"},
                                    org, source="manual")
    transport = Transport()
    sync(transport, org)
    remote_id = contacts.get_contact(item["id"], org)["ghl_contact_id"]
    contacts.update_contact(item["id"], org, {"name": "Local Changed"})
    transport.remote = [{"id": remote_id, "name": "Remote Changed",
                         "email": "both@example.test",
                         "dateUpdated": "2099-03-01T00:00:00Z"}]
    result = sync(transport, org)
    row = contacts.get_contact(item["id"], org)
    check("the newer remote value wins", row["name"], "Remote Changed")
    check("the conflict is reported", bool(result["conflicts"]), True)
    check("the conflict names the field", result["conflicts"][0]["field"], "name")


def test_form_activity_travels_outbound():
    org = new_org("Mirror Activity")
    item = contacts.create_contact({"name": "Form Lead", "email": "form@example.test"},
                                    org, source="form")
    form = forms.create_form({
        "name": "Contact", "kind": "form",
        "questions": [{"id": "email", "label": "Email", "type": "email",
                       "page": 0, "required": True}],
    }, org)
    forms.submit(form, {"email": "form@example.test"}, create_contact=False)
    # The submission above has no contact link; make one like the real form path does.
    with db.get_connection() as conn:
        conn.execute("UPDATE form_responses SET contact_id = ? WHERE org_id = ?",
                     (item["id"], org))
    transport = Transport()
    sync(transport, org)
    upsert = [call for call in transport.calls if "/contacts/upsert" in call["url"]][0]
    activity = {field["id"]: field["value"] for field in upsert["payload"]["customFields"]}
    check("form activity is mirrored", "Contact" in activity.get("cf-activity", ""), True)
    check("the answer is in the activity", "form@example.test" in activity.get("cf-activity", ""), True)


def test_unconfigured_sync_is_a_friendly_error():
    org = orgs_service.create_org({"name": "Mirror Off"})["id"]
    try:
        contact_sync.sync_org(org)
    except contact_sync.SyncError as exc:
        check("the error names AscendPlatform", "AscendPlatform" in str(exc), True)
        check("the error does not name the provider", "GoHighLevel" not in str(exc), True)
    else:
        check("an unconfigured workspace refuses", False, True)


def test_api_permissions_and_branding():
    org = new_org("Mirror API")
    admin = appmod.app.test_client()
    admin.post("/api/auth/login", json={"email": "admin@example.com", "password": "admin123"})
    admin.post("/api/session/org", json={"org_id": org})
    created = admin.post("/api/users", json={
        "email": "mirror-member@example.test", "name": "Mirror Member",
        "password": "member-pass-1234", "role": "member", "org_id": org,
    })
    check("a member can be created for the permission check", created.status_code, 201)
    member = appmod.app.test_client()
    check("the member can sign in",
          member.post("/api/auth/login", json={"email": "mirror-member@example.test",
                                                "password": "member-pass-1234"}).status_code, 200)
    member.post("/api/session/org", json={"org_id": org})
    original = contact_sync.start_sync
    contact_sync.start_sync = lambda org_id, user_id=None: 42
    try:
        denied = member.post("/api/contacts/sync")
        started = admin.post("/api/contacts/sync")
    finally:
        contact_sync.start_sync = original
    check("a member cannot start a sync", denied.status_code, 403)
    check("the API starts a sync", started.status_code, 202)
    check("and returns a job id", (started.get_json() or {}).get("job_id"), 42)
    check("the button is branded", "Sync with AscendPlatform" in open(
        os.path.join(ROOT, "admin", "admin.js"), encoding="utf-8").read(), True)
    script = open(os.path.join(ROOT, "admin", "admin.js"), encoding="utf-8").read()
    check("the old provider label is absent", "Sync with GoHighLevel" not in script, True)
    check("the task is registered", "contact_sync" in jobs.TASKS, True)


if __name__ == "__main__":
    db.init_db()
    test_a_local_contact_is_created_remotely()
    test_a_remote_contact_is_imported_with_all_fields()
    test_remote_changes_pull_and_local_changes_push()
    test_a_simultaneous_change_is_reported_and_newest_wins()
    test_form_activity_travels_outbound()
    test_unconfigured_sync_is_a_friendly_error()
    test_api_permissions_and_branding()
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nCONTACT SYNC OK - two-way merge, activity and the branded control")
