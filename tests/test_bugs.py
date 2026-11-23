"""Agency bug tracker: owner-only CRUD, timeline and status workflow.

Run: .\\venv\\Scripts\\python.exe tests\\test_bugs.py

Every fixture is removed in ``finally``. A client admin and a member must never see the
tracker, and a bug may carry an optional client without becoming visible to that client.
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("NOTIFICATIONS_ENABLED", "0")

import app as appmod  # noqa: E402
import db  # noqa: E402
import orgs as orgs_service  # noqa: E402

TAG = uuid.uuid4().hex[:8]
TITLE = "Bug tracker fixture %s" % TAG
ADMIN_EMAIL = "bug-admin-%s@example.test" % TAG
MEMBER_EMAIL = "bug-member-%s@example.test" % TAG
ORG_NAME = "Bug Tracker Client %s" % TAG
STATE = {}


def login(email, password):
    client = appmod.app.test_client()
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.get_data(as_text=True)
    return client


def owner():
    return login("admin@example.com", "admin123")


def cleanup():
    with db.get_connection() as conn:
        org = conn.execute("SELECT id FROM orgs WHERE name = ?", (ORG_NAME,)).fetchone()
        if org:
            user_ids = [row["id"] for row in conn.execute(
                "SELECT id FROM users WHERE org_id = ?", (org["id"],))]
            if user_ids:
                marks = ",".join("?" for _ in user_ids)
                conn.execute("DELETE FROM bug_events WHERE actor_id IN (%s)" % marks,
                             tuple(user_ids))
                conn.execute("DELETE FROM bug_events WHERE bug_id IN "
                             "(SELECT id FROM bugs WHERE assignee_id IN (%s))" % marks,
                             tuple(user_ids))
                conn.execute("DELETE FROM bugs WHERE assignee_id IN (%s)" % marks,
                             tuple(user_ids))
            conn.execute("DELETE FROM bug_events WHERE bug_id IN "
                         "(SELECT id FROM bugs WHERE org_id = ?)", (org["id"],))
            conn.execute("DELETE FROM bugs WHERE org_id = ?", (org["id"],))
            conn.execute("DELETE FROM users WHERE org_id = ?", (org["id"],))
            conn.execute("DELETE FROM orgs WHERE id = ?", (org["id"],))
        conn.execute("DELETE FROM bugs WHERE title = ?", (TITLE,))
        conn.execute("DELETE FROM bug_events WHERE bug_id NOT IN (SELECT id FROM bugs)")


def setup():
    cleanup()
    db.init_db()
    org = orgs_service.create_org({
        "name": ORG_NAME, "email": ADMIN_EMAIL, "password": "bug-admin-pass-1234",
    })
    STATE["org"] = org
    STATE["admin_client"] = login(ADMIN_EMAIL, "bug-admin-pass-1234")
    with db.get_connection() as conn:
        STATE["admin_id"] = conn.execute(
            "SELECT id FROM users WHERE email = ?", (ADMIN_EMAIL,)
        ).fetchone()["id"]
    member = orgs_service.add_org_user(org["id"], {
        "email": MEMBER_EMAIL, "name": "Bug Member",
        "password": "bug-member-pass-1234", "role": "member",
    })
    STATE["member_email"] = MEMBER_EMAIL


def test_owner_can_report_filter_and_open_a_bug():
    client = owner()
    me = client.get("/api/auth/me").get_json()["user"]
    created = client.post("/api/platform/bugs", json={
        "title": TITLE, "description": "The Publish button does nothing.",
        "priority": "urgent", "org_id": STATE["org"]["id"],
        "assignee_id": me["id"], "page": "#/app/sites",
    })
    assert created.status_code == 201, created.get_data(as_text=True)
    bug = created.get_json()["bug"]
    STATE["bug_id"] = bug["id"]
    assert bug["status"] == "open" and bug["priority"] == "urgent", bug
    assert bug["org_name"] == ORG_NAME, bug
    assert bug["assignee_name"], bug

    listed = client.get("/api/platform/bugs?q=" + TAG)
    assert listed.status_code == 200, listed.get_data(as_text=True)
    body = listed.get_json()
    assert [row["id"] for row in body["bugs"]] == [bug["id"]]
    assert "open" in body["counts"] and "in_progress" in body["counts"]
    assert body["assignees"], body

    detailed = client.get("/api/platform/bugs/%d" % bug["id"])
    assert detailed.status_code == 200, detailed.get_data(as_text=True)
    events = detailed.get_json()["events"]
    assert events and events[0]["action"] == "created", events
    print("owner reported bug #%d and can filter/open it" % bug["id"])


def test_status_priority_assignment_and_comments_are_a_timeline():
    client = owner()
    bug_id = STATE["bug_id"]
    updated = client.put("/api/platform/bugs/%d" % bug_id, json={
        "status": "in_progress", "priority": "high", "description": "Still reproducing.",
    })
    assert updated.status_code == 200, updated.get_data(as_text=True)
    assert updated.get_json()["bug"]["status"] == "in_progress"
    assert updated.get_json()["bug"]["priority"] == "high"

    commented = client.post("/api/platform/bugs/%d/comments" % bug_id,
                            json={"comment": "Reproduced on staging."})
    assert commented.status_code == 200, commented.get_data(as_text=True)
    assert any(ev["action"] == "commented" for ev in commented.get_json()["events"])

    resolved = client.put("/api/platform/bugs/%d" % bug_id, json={"status": "resolved"})
    assert resolved.status_code == 200, resolved.get_data(as_text=True)
    bug = resolved.get_json()["bug"]
    assert bug["status"] == "resolved" and bug["resolved_at"], bug
    reopened = client.put("/api/platform/bugs/%d" % bug_id, json={"status": "open"})
    assert reopened.get_json()["bug"]["resolved_at"] is None
    actions = [event["action"] for event in reopened.get_json()["events"]]
    assert "status" in actions and "priority" in actions and "commented" in actions, actions
    print("status, priority and comments are all recorded in one timeline")


def test_validation_and_permissions():
    client = owner()
    bad_priority = client.post("/api/platform/bugs", json={
        "title": "Bad priority", "priority": "whenever",
    })
    assert bad_priority.status_code == 400, bad_priority.get_data(as_text=True)
    bad_org = client.post("/api/platform/bugs", json={
        "title": "Bad org", "org_id": 999999999,
    })
    assert bad_org.status_code == 400, bad_org.get_data(as_text=True)
    bad_assignee = client.post("/api/platform/bugs", json={
        "title": "Bad assignee", "assignee_id": STATE["admin_id"],
    })
    assert bad_assignee.status_code == 400, bad_assignee.get_data(as_text=True)

    for person in (STATE["admin_client"], login(MEMBER_EMAIL, "bug-member-pass-1234")):
        assert person.get("/api/platform/bugs").status_code == 403
        assert person.post("/api/platform/bugs", json={"title": "Nope"}).status_code == 403
        assert person.get("/api/platform/bugs/%d" % STATE["bug_id"]).status_code == 403

    anon = appmod.app.test_client()
    assert anon.get("/api/platform/bugs").status_code == 401
    assert anon.post("/api/platform/bugs", json={"title": "Nope"}).status_code == 401
    print("invalid values refused; clients and members cannot reach the tracker")


def test_the_console_exposes_the_tracker():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "admin", "admin.js"), encoding="utf-8") as handle:
        script = handle.read()
    for needle in ("id: 'bugs', label: 'Bug tracker'", "#/platform/bugs",
                   "function viewBugTracker", "+ Report bug", "bug-timeline",
                   "Add comment", "Bug tracker"):
        assert needle in script, needle
    with open(os.path.join(root, "admin", "admin.css"), encoding="utf-8") as handle:
        css = handle.read()
    assert ".bug-summary" in css and ".bug-table" in css and ".bug-timeline" in css
    print("platform Bug tracker UI is wired")


def main():
    try:
        setup()
        test_owner_can_report_filter_and_open_a_bug()
        test_status_priority_assignment_and_comments_are_a_timeline()
        test_validation_and_permissions()
        test_the_console_exposes_the_tracker()
        print("\nBUG TRACKER OK")
    finally:
        cleanup()


if __name__ == "__main__":
    main()
