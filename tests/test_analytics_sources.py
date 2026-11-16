"""Analytics source access is workspace-scoped, and members are read-only.

Run: .\\venv\\Scripts\\python.exe tests\\test_analytics_sources.py

The old API had two boundary failures: a member could see a client_admin-only page but
receive a 403 from its data, and a source update by id did not check the source's org.
This suite pins the intended model: client admins manage their own sources, members read
analytics and sources, and the operator can act on any workspace.
"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="analytics-sources-"), "test.db").replace("\\", "/")
os.environ.pop("ALLOW_LIVE_ACTIONS", None)
os.environ.pop("JOB_BACKEND", None)
os.environ.pop("REDIS_URL", None)

import app as appmod  # noqa: E402
import db  # noqa: E402
import orgs as orgs_service  # noqa: E402

db.init_db()

FAILED = []
ALPHA = {
    "name": "Analytics Alpha", "slug": "analytics-alpha",
    "email": "alpha@analytics.test", "password": "alpha-pass-1234",
}
BETA = {
    "name": "Analytics Beta", "slug": "analytics-beta",
    "email": "beta@analytics.test", "password": "beta-pass-1234",
}
MEMBER = {
    "email": "member@analytics.test", "password": "member-pass-1234",
    "name": "Read Only",
}


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


def login(email, password):
    client = appmod.app.test_client()
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    check("sign in as %s" % email, response.status_code, 200)
    return client


def create_source(client, external_id="123456789", label="Main website"):
    return client.post("/api/data-sources", json={
        "kind": "ga4", "external_id": external_id, "label": label,
        "credential_ref": "",
    })


def main():
    alpha = orgs_service.create_org(ALPHA)
    beta = orgs_service.create_org(BETA)
    orgs_service.add_org_user(alpha["id"], MEMBER)
    a = login(ALPHA["email"], ALPHA["password"])
    b = login(BETA["email"], BETA["password"])
    member = login(MEMBER["email"], MEMBER["password"])
    owner = login("admin@example.com", "admin123")

    created = create_source(a)
    check("a client admin can connect GA4", created.status_code, 201)
    source = (created.get_json() or {}).get("source") or {}
    check("the source belongs to their workspace", source.get("org_id"), alpha["id"])

    listed = a.get("/api/data-sources").get_json() or {}
    check("the client sees its source", len(listed.get("sources") or []), 1)
    check("the client may manage its sources", listed.get("can_manage"), True)

    b_listed = b.get("/api/data-sources").get_json() or {}
    check("another workspace sees none", b_listed.get("sources"), [])
    blocked = b.post("/api/data-sources", json={
        "id": source["id"], "kind": "ga4", "external_id": "999",
        "credential_ref": "",
    })
    check("another workspace cannot edit it by id", blocked.status_code, 404)
    still = a.get("/api/data-sources").get_json()["sources"][0]
    check("the source was not changed", still["external_id"], "123456789")

    updated = a.post("/api/data-sources", json={
        "id": source["id"], "kind": "ga4", "external_id": "987654321",
        "label": "Updated", "credential_ref": "",
    })
    check("the owning workspace can edit it", updated.status_code, 200)
    check("the edit is visible", updated.get_json()["source"]["external_id"], "987654321")

    # A member can read the brief and the source list, but may not mutate either.
    summary = member.get("/api/analytics/summary?days=7")
    check("a member can read analytics", summary.status_code, 200)
    member_sources = member.get("/api/data-sources").get_json() or {}
    check("a member can read source labels", len(member_sources.get("sources") or []), 1)
    check("a member cannot manage sources", member_sources.get("can_manage"), False)
    denied = create_source(member, external_id="555")
    check("a member cannot connect a source", denied.status_code, 403)

    # A login with no workspace must not fall through to the unscoped source list.
    made = owner.post("/api/users", json={
        "email": "no-org@analytics.test", "name": "No Workspace",
        "password": "no-org-pass-1234", "role": "member",
    })
    check("the operator can create a legacy no-org login", made.status_code, 201)
    nobody = login("no-org@analytics.test", "no-org-pass-1234")
    no_org = nobody.get("/api/data-sources").get_json() or {}
    check("a no-org login gets an empty source list", no_org.get("sources"), [])
    check("and cannot manage sources", no_org.get("can_manage"), False)

    # The operator keeps the cross-workspace view needed for support.
    owner_sources = owner.get("/api/data-sources?org_id=%d" % alpha["id"]).get_json() or {}
    check("the operator can inspect a workspace's sources",
          len(owner_sources.get("sources") or []), 1)

    # The UI must offer the client a real path, not the old operator-only instruction.
    js = open(os.path.join(ROOT, "admin", "admin.js"), encoding="utf-8").read()
    check("Analytics has a traffic-source card", "Traffic sources" in js, True)
    check("the client gets a connect action", "Connect analytics" in js, True)
    check("the old dead-end copy is gone",
          "Sync site logs, or connect a GA4 property for this client" in js, False)

    with db.get_connection() as conn:
        conn.execute("DELETE FROM users WHERE org_id IN (?, ?)", (alpha["id"], beta["id"]))
        conn.execute("DELETE FROM users WHERE email = ?", ("no-org@analytics.test",))
        conn.execute("DELETE FROM data_sources WHERE org_id IN (?, ?)",
                     (alpha["id"], beta["id"]))
        conn.execute("DELETE FROM business_profiles WHERE org_id IN (?, ?)",
                     (alpha["id"], beta["id"]))
        conn.execute("DELETE FROM orgs WHERE id IN (?, ?)", (alpha["id"], beta["id"]))

    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nANALYTICS SOURCES OK - workspace-scoped and member read-only")


if __name__ == "__main__":
    main()
