"""Automations over HTTP: validation, tenancy and the run handoff.

The graph tests cover the validator in isolation; this covers the thing a validator cannot
- that one tenant's automation is invisible to another. Tenancy is asserted against a
second org id that the logged-in session does not own, which needs no second login.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import automations  # noqa: E402
import jobs  # noqa: E402
from app import app  # noqa: E402

CHAIN = [
    {"id": "a", "type": "agent", "title": "Draft", "next": ["b"]},
    {"id": "b", "type": "gate", "title": "Approve", "next": ["c"]},
    {"id": "c", "type": "notify", "title": "Report", "next": ["d"]},
    {"id": "d", "type": "stop", "title": "Done", "next": []},
]
OTHER_ORG = 999999          # no such client: anything owned by it must stay hidden


def login(client):
    resp = client.post("/api/auth/login",
                       json={"email": "admin@example.com", "password": "admin123"})
    assert resp.status_code == 200, resp.get_json()


def test_validation_lives_below_http_and_refuses_outward_steps():
    """Validation is enforced in the service, so it holds for every caller - HTTP, the
    manager's start_job tool, or a future drafter - not just the routes."""
    looping = [{"id": "a", "type": "agent", "title": "A", "next": ["b"]},
               {"id": "b", "type": "agent", "title": "B", "next": ["a"]}]
    try:
        automations.create(OTHER_ORG, "Loop", looping)
    except automations.AutomationError as exc:
        assert "loop" in str(exc).lower(), exc
    else:
        raise AssertionError("a looping graph was stored")

    ungated = [{"id": "send", "type": "tool", "title": "Send", "next": [],
                "config": {"tool": "send_email"}}]
    try:
        automations.create(OTHER_ORG, "Send", ungated)
    except automations.AutomationError as exc:
        assert "gate" in str(exc).lower(), exc
    else:
        raise AssertionError("an ungated outward step was stored")
    print("loop and ungated send refused by the service")


def test_another_tenants_automation_is_invisible():
    other_id = automations.create(OTHER_ORG, "Not yours", CHAIN)
    client = app.test_client()
    login(client)

    assert client.get("/api/automations/%d" % other_id).status_code == 404
    assert client.put("/api/automations/%d" % other_id,
                      json={"name": "Mine now"}).status_code == 404
    assert client.delete("/api/automations/%d" % other_id).status_code == 404
    names = [a["name"] for a in client.get("/api/automations").get_json()["automations"]]
    assert "Not yours" not in names, names
    assert automations.get(other_id, OTHER_ORG)["name"] == "Not yours"
    print("cross-tenant access refused, item intact")


def test_operator_without_a_client_is_refused_not_crashed():
    client = app.test_client()
    login(client)
    resp = client.post("/api/automations", json={"name": "Orphan", "steps": CHAIN})
    assert resp.status_code == 400, (resp.status_code, resp.get_json())
    assert "client" in resp.get_json()["error"].lower()
    print("no-client create refused:", resp.get_json()["error"][:50])


def test_run_queues_a_run_and_submits_the_job():
    mine = automations.create(OTHER_ORG, "Smoke run", CHAIN)
    submitted = []
    original = jobs.submit
    jobs.submit = lambda job_id: submitted.append(job_id)
    try:
        queued = automations.start(mine, OTHER_ORG)
    finally:
        jobs.submit = original

    assert queued["run_id"] and queued["job_id"], queued
    assert submitted == [queued["job_id"]], submitted   # created *and* submitted
    job = jobs.get_job(queued["job_id"])
    assert job["task"] == "automation", job["task"]
    print("run queued as job #%d and submitted" % queued["job_id"])
    automations.delete(mine, OTHER_ORG)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("AUTOMATIONS SMOKE OK")
