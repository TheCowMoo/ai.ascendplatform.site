"""User management across the two planes: agency People and a client's Team.

Run: .\\venv\\Scripts\\python.exe tests\\test_user_management.py

The tests use throwaway clients and remove every row they create. They exercise the
security boundary, not just the happy path: a client admin must never reach another
client, a member must never reach either management API, a suspended account must lose
its existing session, and the last owner/admin must be impossible to strand.
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("NOTIFICATIONS_ENABLED", "0")

import app as appmod  # noqa: E402
import db  # noqa: E402
import invites as invites_service  # noqa: E402
import orgs as orgs_service  # noqa: E402
import user_service  # noqa: E402

TAG = uuid.uuid4().hex[:8]
ORG_NAME = "User Management Test %s" % TAG
ORG_SLUG = "user-management-%s" % TAG
ADMIN_EMAIL = "um-admin-%s@example.test" % TAG
ADMIN_PASSWORD = "um-admin-pass-1234"
MEMBER_EMAIL = "um-member-%s@example.test" % TAG
SECOND_ADMIN_EMAIL = "um-second-%s@example.test" % TAG
INVITE_EMAIL = "um-invite-%s@example.test" % TAG

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
        org = conn.execute("SELECT id FROM orgs WHERE slug = ?", (ORG_SLUG,)).fetchone()
        if org:
            conn.execute("DELETE FROM user_events WHERE org_id = ?", (org["id"],))
            conn.execute("DELETE FROM invites WHERE org_id = ?", (org["id"],))
            conn.execute("DELETE FROM users WHERE org_id = ?", (org["id"],))
            conn.execute("DELETE FROM orgs WHERE id = ?", (org["id"],))
        other = conn.execute(
            "SELECT id FROM orgs WHERE name = ?", ("User Management Other %s" % TAG,)
        ).fetchone()
        if other:
            conn.execute("DELETE FROM users WHERE org_id = ?", (other["id"],))
            conn.execute("DELETE FROM orgs WHERE id = ?", (other["id"],))
        for email in (ADMIN_EMAIL, MEMBER_EMAIL, SECOND_ADMIN_EMAIL, INVITE_EMAIL):
            conn.execute("DELETE FROM users WHERE email = ?", (email,))
            conn.execute("DELETE FROM invites WHERE email = ?", (email,))


def setup():
    cleanup()
    db.init_db()
    org = orgs_service.create_org({
        "name": ORG_NAME, "slug": ORG_SLUG,
        "email": ADMIN_EMAIL, "password": ADMIN_PASSWORD,
    })
    assert org, "could not create the test client"
    STATE["org"] = org
    with db.get_connection() as conn:
        STATE["admin_id"] = conn.execute(
            "SELECT id FROM users WHERE email = ?", (ADMIN_EMAIL,)
        ).fetchone()["id"]

    created = owner().post("/api/platform/users", json={
        "email": MEMBER_EMAIL, "name": "UM Member", "password": "um-member-pass-1234",
        "role": "member", "org_id": org["id"],
    })
    assert created.status_code == 201, created.get_data(as_text=True)
    STATE["member_id"] = created.get_json()["user"]["id"]
    STATE["member_password"] = "um-member-pass-1234"


def test_owner_directory_is_agency_wide():
    org = STATE["org"]
    client = owner()
    response = client.get("/api/platform/users")
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    emails = {row["email"] for row in body["users"]}
    assert ADMIN_EMAIL in emails and MEMBER_EMAIL in emails
    assert any(row["email"] == "admin@example.com" for row in body["users"])
    assert any(event["action"] == "create" for event in body["events"])

    scoped = client.get("/api/platform/users?org_id=%d" % org["id"])
    assert scoped.status_code == 200, scoped.get_data(as_text=True)
    scoped_emails = {row["email"] for row in scoped.get_json()["users"]}
    assert scoped_emails == {ADMIN_EMAIL, MEMBER_EMAIL}, scoped_emails
    assert all(row["org_id"] == org["id"] for row in scoped.get_json()["users"])
    print("owner sees the agency and can filter by client")


def test_client_admin_can_only_manage_their_own_client():
    org = STATE["org"]
    other = orgs_service.create_org({"name": "User Management Other %s" % TAG})
    STATE["other_org"] = other
    client = login(ADMIN_EMAIL, ADMIN_PASSWORD)

    listed = client.get("/api/users")
    assert listed.status_code == 200, listed.get_data(as_text=True)
    assert {row["email"] for row in listed.get_json()["users"]} == {ADMIN_EMAIL, MEMBER_EMAIL}

    made = client.post("/api/users", json={
        "email": "um-own-%s@example.test" % TAG, "name": "Own Staff",
        "password": "um-own-pass-1234", "role": "member",
    })
    assert made.status_code == 201, made.get_data(as_text=True)
    assert made.get_json()["user"]["org_id"] == org["id"], made.get_json()
    STATE["own_user_id"] = made.get_json()["user"]["id"]

    owner_role = client.post("/api/users", json={
        "email": "um-owner-%s@example.test" % TAG, "name": "Owner Attempt",
        "password": "um-owner-pass-1234", "role": "owner",
    })
    assert owner_role.status_code == 403, owner_role.get_data(as_text=True)

    cross_org = client.post("/api/users", json={
        "email": "um-cross-%s@example.test" % TAG, "name": "Cross Org",
        "password": "um-cross-pass-1234", "role": "member", "org_id": other["id"],
    })
    assert cross_org.status_code == 403, cross_org.get_data(as_text=True)
    print("client admin is confined to its org and cannot create an owner")


def test_members_have_no_management_api():
    member = login(MEMBER_EMAIL, STATE["member_password"])
    for method, path, body in (
        ("get", "/api/users", None),
        ("post", "/api/users", {"email": "x@example.test", "password": "password-123"}),
        ("get", "/api/invites", None),
    ):
        response = getattr(member, method)(path, json=body) if body else getattr(member, method)(path)
        assert response.status_code == 403, (method, path, response.status_code)
    print("members are refused the management APIs")


def test_role_changes_apply_to_an_existing_session():
    org = STATE["org"]
    member = login(MEMBER_EMAIL, STATE["member_password"])
    assert member.get("/api/users").status_code == 403

    promoted = owner().put("/api/platform/users/%d" % STATE["member_id"],
                            json={"role": "client_admin"})
    assert promoted.status_code == 200, promoted.get_data(as_text=True)
    # The cookie still says "member"; the server must reload the row and let this in.
    assert member.get("/api/users").status_code == 200

    demoted = owner().put("/api/platform/users/%d" % STATE["member_id"],
                          json={"role": "member"})
    assert demoted.status_code == 200, demoted.get_data(as_text=True)
    assert member.get("/api/users").status_code == 403
    print("promotion/demotion takes effect without a new login")


def test_suspend_stops_the_next_request_and_login():
    member = login(MEMBER_EMAIL, STATE["member_password"])
    suspended = owner().post("/api/platform/users/%d/status" % STATE["member_id"],
                             json={"status": "suspended"})
    assert suspended.status_code == 200, suspended.get_data(as_text=True)
    assert member.get("/api/sites").status_code == 403
    fresh = appmod.app.test_client().post(
        "/api/auth/login",
        json={"email": MEMBER_EMAIL, "password": STATE["member_password"]},
    )
    assert fresh.status_code == 403, fresh.get_data(as_text=True)

    enabled = owner().post("/api/platform/users/%d/status" % STATE["member_id"],
                           json={"status": "active"})
    assert enabled.status_code == 200, enabled.get_data(as_text=True)
    # A suspended request clears the cookie, so the restored account signs in again.
    assert login(MEMBER_EMAIL, STATE["member_password"]).get("/api/sites").status_code == 200
    print("suspend blocks the existing session and a fresh login")


def test_the_last_client_admin_cannot_be_stranded():
    org = STATE["org"]
    client = owner()
    # The first admin is currently the only active admin of this client.
    blocked = client.post("/api/platform/users/%d/status" % STATE["admin_id"],
                           json={"status": "suspended"})
    assert blocked.status_code == 400, blocked.get_data(as_text=True)
    assert "at least one active client admin" in blocked.get_json()["error"]

    second = client.post("/api/platform/users", json={
        "email": SECOND_ADMIN_EMAIL, "name": "Second Admin",
        "password": "um-second-pass-1234", "role": "client_admin", "org_id": org["id"],
    })
    assert second.status_code == 201, second.get_data(as_text=True)
    STATE["second_admin_id"] = second.get_json()["user"]["id"]

    allowed = client.post("/api/platform/users/%d/status" % STATE["admin_id"],
                          json={"status": "suspended"})
    assert allowed.status_code == 200, allowed.get_data(as_text=True)
    restored = client.post("/api/platform/users/%d/status" % STATE["admin_id"],
                           json={"status": "active"})
    assert restored.status_code == 200, restored.get_data(as_text=True)
    print("the last client admin cannot be suspended")


def test_the_last_owner_cannot_remove_themselves():
    client = owner()
    me = client.get("/api/auth/me").get_json()["user"]
    response = client.delete("/api/platform/users/%d" % me["id"])
    assert response.status_code == 400, response.get_data(as_text=True)
    assert "your own account" in response.get_json()["error"]
    print("an owner cannot delete their own login")


def test_password_reset_and_event_audit():
    client = owner()
    response = client.post("/api/platform/users/%d/password" % STATE["member_id"],
                           json={"new_password": "um-reset-pass-1234"})
    assert response.status_code == 200, response.get_data(as_text=True)
    login(MEMBER_EMAIL, "um-reset-pass-1234")
    with db.get_connection() as conn:
        events = conn.execute(
            "SELECT action FROM user_events WHERE target_id = ? ORDER BY id",
            (STATE["member_id"],),
        ).fetchall()
    actions = [row["action"] for row in events]
    assert "create" in actions and "password" in actions, actions
    print("password reset is audited")


def test_invites_are_scoped_and_normalise_the_old_role():
    org = STATE["org"]
    invite, _token = invites_service.create(
        INVITE_EMAIL, name="Legacy Invite", org_id=org["id"], role="client_member",
    )
    assert invite["role"] == "member", invite
    STATE["invite_id"] = invite["id"]

    client = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    listed = client.get("/api/invites")
    assert listed.status_code == 200, listed.get_data(as_text=True)
    assert all(row["org_id"] == org["id"] for row in listed.get_json()["invites"])

    other = STATE["other_org"]
    sent = client.post("/api/invites", json={
        "email": "um-scope-%s@example.test" % TAG,
        "name": "Scope Invite", "role": "member", "org_id": other["id"],
    })
    # Delivery may fail when the developer has no GHL sender. Either way the invite
    # must belong to the client admin's own org, never the org supplied in the body.
    assert sent.status_code in (201, 502), sent.get_data(as_text=True)
    assert sent.get_json()["invite"]["org_id"] == org["id"], sent.get_json()

    member = login(MEMBER_EMAIL, "um-reset-pass-1234")
    assert member.post("/api/invites", json={
        "email": "um-denied-%s@example.test" % TAG, "role": "member",
    }).status_code == 403
    print("client scoping and client_member normalization hold")


def test_the_console_exposes_both_management_planes():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "admin", "admin.js"), encoding="utf-8") as handle:
        script = handle.read()
    assert "id: 'people', label: 'People'" in script
    assert "#/platform/people" in script
    assert "function viewPlatformPeople" in script
    assert "Agency owner" in script
    assert "Client admin" in script
    assert "'client_member'" not in script.split("function invitePersonModal")[1].split(
        "function viewUsers")[0]
    assert "function viewUsers" in script
    assert "Team" in script
    with open(os.path.join(root, "admin", "admin.css"), encoding="utf-8") as handle:
        css = handle.read()
    assert ".badge-danger" in css and ".people-table" in css
    print("platform People and account Team controls are present")


def main():
    try:
        setup()
        test_owner_directory_is_agency_wide()
        test_client_admin_can_only_manage_their_own_client()
        test_members_have_no_management_api()
        test_role_changes_apply_to_an_existing_session()
        test_suspend_stops_the_next_request_and_login()
        test_the_last_client_admin_cannot_be_stranded()
        test_the_last_owner_cannot_remove_themselves()
        test_password_reset_and_event_audit()
        test_invites_are_scoped_and_normalise_the_old_role()
        test_the_console_exposes_both_management_planes()
        print("\nUSER MANAGEMENT OK")
    finally:
        cleanup()


if __name__ == "__main__":
    main()
