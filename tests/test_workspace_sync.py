r"""The workspace-object mirror: custom object schema, records and idempotency.

Run: .\venv\Scripts\python.exe tests\test_workspace_sync.py
"""

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="workspace-mirror-"), "test.db").replace(
    os.sep, "/")
os.environ.pop("ALLOW_LIVE_ACTIONS", None)
os.environ.pop("JOB_BACKEND", None)
os.environ.pop("REDIS_URL", None)

import automations  # noqa: E402
import contacts  # noqa: E402
import db  # noqa: E402
import deliverables  # noqa: E402
import forms  # noqa: E402
import orgs as orgs_service  # noqa: E402
import platform_mail  # noqa: E402
import settings  # noqa: E402
import sites as sites_service  # noqa: E402
import workspace_sync  # noqa: E402

FAILED = []


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


class Transport:
    def __init__(self):
        self.calls = []
        self.objects = []
        self.fields = set()
        self.records = {}
        self.sequence = 0

    def __call__(self, method, url, headers, payload=None, timeout=20):
        self.calls.append({"method": method, "url": url, "payload": payload})
        if url.rsplit("?", 1)[0].endswith("/objects"):
            if method == "GET":
                return 200, json.dumps({"objects": self.objects})
            self.objects.append({"id": "obj-1", "key": workspace_sync.SCHEMA_KEY})
            return 201, json.dumps({"object": {"id": "obj-1",
                                              "key": workspace_sync.SCHEMA_KEY}})
        if "/objects/" in url and "fetchProperties=true" in url:
            return 200, json.dumps({"object": {
                "fields": [{"fieldKey": key} for key in sorted(self.fields)]}})
        if "/custom-fields" in url:
            self.fields.add(payload["fieldKey"])
            return 201, json.dumps({"field": {"id": "field-%d" % len(self.fields)}})
        if method == "POST" and url.endswith("/records"):
            self.sequence += 1
            remote_id = "record-%d" % self.sequence
            self.records[remote_id] = payload["properties"]
            return 201, json.dumps({"record": {"id": remote_id}})
        if method == "PUT" and "/records/" in url:
            remote_id = url.split("/records/", 1)[1].split("?", 1)[0]
            self.records[remote_id] = payload["properties"]
            return 200, json.dumps({"record": {"id": remote_id}})
        return 404, json.dumps({"message": "not found"})


def new_org(name):
    org = orgs_service.create_org({"name": name})["id"]
    settings.set_value("GHL_CLIENT_API_TOKEN", "test-token", actor="test", org_id=org)
    settings.set_value("GHL_CLIENT_LOCATION_ID", "test-location", actor="test", org_id=org)
    settings.forget()
    return org


def build_workspace(org):
    domain = "mirror-%s.test" % org
    site = sites_service.create_site({"domain": domain}, org_id=org)
    deliverables.create_deliverable({
        "kind": "website", "title": "Mirror home", "slug": "home",
        "summary": "A home page", "status": "published",
        "content": "<html><body>Mirror</body></html>", "site_id": site["id"],
    }, org_id=org)
    form = forms.create_form({
        "name": "Mirror form", "kind": "form",
        "questions": [{"id": "email", "label": "Email", "type": "email",
                       "page": 0, "required": True}],
    }, org)
    forms.submit(form, {"email": "mirror@example.test"}, create_contact=False)
    automations.create(org, "Mirror automation", [
        {"id": "start", "type": "notify", "title": "Start", "next": ["end"],
         "config": {"message": "hello"}},
        {"id": "end", "type": "stop", "title": "Done", "next": []},
    ])
    return site, form


def sync(transport, org):
    original = platform_mail._transport
    platform_mail._transport = transport
    try:
        return workspace_sync.sync_org(org)
    finally:
        platform_mail._transport = original


def test_schema_and_records_are_created_then_skipped():
    org = new_org("Workspace Mirror")
    build_workspace(org)
    transport = Transport()
    result = sync(transport, org)
    check("the schema is created", any(c["url"].endswith("/objects") and
                                       c["method"] == "POST" for c in transport.calls), True)
    check("the title field is defined", any("ascendplatform_item.title" in key
                                            for key in transport.fields), True)
    check("records are created", result["created"], 5)
    check("none failed", result["failed"], [])
    check("the record count is reported", result["total"], 5)
    check("five mappings are stored", len(transport.records), 5)
    titles = sorted(record["title"] for record in transport.records.values())
    check("the website and form are visible",
          "Mirror home" in titles and "Mirror form" in titles, True)
    check("the automation is visible", "Mirror automation" in titles, True)

    second = sync(transport, org)
    check("a no-change run skips every record", second["skipped"], 5)
    check("and creates nothing", second["created"], 0)


def test_a_changed_record_updates_in_place():
    org = new_org("Workspace Update")
    build_workspace(org)
    transport = Transport()
    sync(transport, org)
    page = deliverables.list_deliverables("website", org)[0]
    deliverables.update_deliverable(page["id"], {"title": "Changed home"})
    result = sync(transport, org)
    check("the changed record updates", result["updated"], 1)
    check("the other records are untouched", result["skipped"], 4)
    check("the remote title changed",
          "Changed home" in [r["title"] for r in transport.records.values()], True)


def test_schema_failure_is_reported_not_hidden():
    org = new_org("Workspace Denied")
    original = platform_mail._transport

    def denied(method, url, headers, payload=None, timeout=20):
        return 403, json.dumps({"message": "objects_scope_missing"})

    platform_mail._transport = denied
    try:
        result = workspace_sync.sync_org(org)
    finally:
        platform_mail._transport = original
    check("a schema failure is reported", result["ok"], False)
    check("with a readable error", bool(result["error"]), True)
    check("and no provider leak", "GoHighLevel" not in result["error"], True)


def test_unconfigured_workspace_is_a_friendly_error():
    org = orgs_service.create_org({"name": "Workspace Off"})["id"]
    try:
        workspace_sync.sync_org(org)
    except workspace_sync.MirrorError as exc:
        check("unconfigured is named", "AscendPlatform" in str(exc), True)
    else:
        check("unconfigured workspace refuses", False, True)


if __name__ == "__main__":
    db.init_db()
    test_schema_and_records_are_created_then_skipped()
    test_a_changed_record_updates_in_place()
    test_schema_failure_is_reported_not_hidden()
    test_unconfigured_workspace_is_a_friendly_error()
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nWORKSPACE MIRROR OK - schema, records, updates and permission failures")
