r"""Native opportunities from AscendPlatform contacts.

Run: .\venv\Scripts\python.exe tests\test_opportunity_sync.py
"""

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="opportunity-sync-"), "test.db").replace(
    os.sep, "/")
os.environ.pop("ALLOW_LIVE_ACTIONS", None)
os.environ.pop("JOB_BACKEND", None)
os.environ.pop("REDIS_URL", None)

import contacts  # noqa: E402
import db  # noqa: E402
import opportunity_sync  # noqa: E402
import orgs as orgs_service  # noqa: E402
import platform_mail  # noqa: E402
import settings  # noqa: E402

FAILED = []


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


class Transport:
    def __init__(self, pipelines=None):
        self.calls = []
        self.pipelines = pipelines if pipelines is not None else [
            {"id": "pipe-1", "name": "Leads", "stages": [
                {"id": "stage-1", "name": "New", "position": 1},
                {"id": "stage-2", "name": "Won", "position": 2},
            ]},
        ]
        self.opportunities = {}
        self.sequence = 0

    def __call__(self, method, url, headers, payload=None, timeout=20):
        self.calls.append({"method": method, "url": url, "headers": headers,
                           "payload": payload})
        if "/opportunities/pipelines" in url:
            return 200, json.dumps({"pipelines": self.pipelines})
        if url.endswith("/opportunities/upsert"):
            self.sequence += 1
            remote_id = (payload or {}).get("id") or "opp-%d" % self.sequence
            self.opportunities[remote_id] = dict(payload or {})
            return 201, json.dumps({"opportunity": {"id": remote_id},
                                    "new": not bool((payload or {}).get("id"))})
        return 404, json.dumps({"message": "not found"})


def new_org(name, pipeline=True):
    org = orgs_service.create_org({"name": name})["id"]
    settings.set_value("GHL_CLIENT_API_TOKEN", "test-token", actor="test", org_id=org)
    settings.set_value("GHL_CLIENT_LOCATION_ID", "test-location", actor="test", org_id=org)
    settings.set_value("GHL_CLIENT_OPPORTUNITIES", "1", actor="test", org_id=org)
    if pipeline:
        settings.set_value("GHL_CLIENT_OPPORTUNITY_PIPELINE_ID", "pipe-1",
                           actor="test", org_id=org)
        settings.set_value("GHL_CLIENT_OPPORTUNITY_STAGE_ID", "stage-1",
                           actor="test", org_id=org)
    settings.forget()
    return org


def add_contact(org, name, remote=True, status="new"):
    item = contacts.create_contact({"name": name, "email": "%s@example.test" % name.lower().replace(" ", ".")},
                                   org, source="form")
    if remote:
        item = contacts.update_contact(item["id"], org, {"ghl_contact_id": "remote-%d" % item["id"],
                                                          "status": status})
    return item


def sync(transport, org):
    original = platform_mail._transport
    platform_mail._transport = transport
    try:
        return opportunity_sync.sync_org(org)
    finally:
        platform_mail._transport = original


def test_a_contact_becomes_an_opportunity():
    org = new_org("Opportunity Create")
    contact = add_contact(org, "Ada Lovelace")
    transport = Transport()
    result = sync(transport, org)
    check("the opportunity is created", result["created"], 1)
    upsert = [call for call in transport.calls if call["url"].endswith("/opportunities/upsert")][0]
    check("the v3 header is used", upsert["headers"]["Version"], "v3")
    check("the contact is linked", upsert["payload"]["contactId"], contact["ghl_contact_id"])
    check("the configured pipeline is used", upsert["payload"]["pipelineId"], "pipe-1")
    check("the stage is used", upsert["payload"]["pipelineStageId"], "stage-1")
    check("a new lead is open", upsert["payload"]["status"], "open")

    second = sync(transport, org)
    check("a no-change contact is skipped", second["skipped"], 1)
    check("nothing is created twice", len(transport.opportunities), 1)


def test_status_changes_update_the_same_opportunity():
    org = new_org("Opportunity Update")
    contact = add_contact(org, "Grace Hopper")
    transport = Transport()
    sync(transport, org)
    remote_id = list(transport.opportunities)[0]
    contacts.update_contact(contact["id"], org, {"status": "customer"})
    result = sync(transport, org)
    check("the opportunity is updated", result["updated"], 1)
    check("the same remote row is reused", list(transport.opportunities), [remote_id])
    check("a customer is won", transport.opportunities[remote_id]["status"], "won")


def test_pipeline_is_discovered_when_not_configured():
    org = new_org("Opportunity Discovery", pipeline=False)
    add_contact(org, "Katherine Johnson")
    transport = Transport()
    result = sync(transport, org)
    check("the first pipeline is discovered", result["created"], 1)
    upsert = [call for call in transport.calls if call["url"].endswith("/opportunities/upsert")][0]
    check("and its first stage is used", upsert["payload"]["pipelineStageId"], "stage-1")


def test_missing_remote_contact_is_skipped():
    org = new_org("Opportunity Skip")
    add_contact(org, "No Remote", remote=False)
    result = sync(Transport(), org)
    check("a contact without a remote id is skipped", result["skipped"], 1)
    check("nothing failed", result["failed"], [])


def test_missing_pipeline_is_a_readable_warning():
    org = new_org("Opportunity No Pipeline", pipeline=False)
    add_contact(org, "No Pipeline")
    result = sync(Transport(pipelines=[]), org)
    check("a missing pipeline is reported", result["ok"], False)
    check("the message is readable", bool(result["error"]), True)
    check("the provider is not named", "GoHighLevel" not in result["error"], True)


def test_the_switch_turns_the_mirror_off():
    org = new_org("Opportunity Off")
    settings.set_value("GHL_CLIENT_OPPORTUNITIES", "0", actor="test", org_id=org)
    settings.forget()
    add_contact(org, "Off Contact")
    result = sync(Transport(), org)
    check("the switch is respected", result["total"], 0)
    check("and explained", "switched off" in result["warnings"][0], True)


if __name__ == "__main__":
    db.init_db()
    test_a_contact_becomes_an_opportunity()
    test_status_changes_update_the_same_opportunity()
    test_pipeline_is_discovered_when_not_configured()
    test_missing_remote_contact_is_skipped()
    test_missing_pipeline_is_a_readable_warning()
    test_the_switch_turns_the_mirror_off()
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nOPPORTUNITY SYNC OK - pipeline, create/update, skip and switch")
