"""Tenancy isolation (stage S7).

Run: .\\venv\\Scripts\\python.exe tests\\smoke_tenancy.py

Two clients are created, each with its own admin, and then every cross-tenant
operation is attempted *as an attack*: each one must fail. The expected answer for
another client's row is 404, not 403 - a 403 would confirm the row exists, which is
itself a leak.

The platform operator is exercised too: it must still see across every client, or
the change would have broken the console it replaces.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.pop("ALLOW_LIVE_ACTIONS", None)

import app as appmod  # noqa: E402
import orgs as orgs_service  # noqa: E402
import sites as sites_service  # noqa: E402

# Re-runnable: these are removed at the start and the end of every run.
ALPHA = {
    "name": "Tenancy Alpha", "slug": "tenancy-alpha", "plan": "pro",
    "email": "alpha@tenancy.test", "password": "alpha-pass-1234",
}
BETA = {
    "name": "Tenancy Beta", "slug": "tenancy-beta",
    "email": "beta@tenancy.test", "password": "beta-pass-1234",
}
DOMAINS = ("alpha.tenancy.test",)

# A login that belongs to no client. The Users tab creates these when org_id is
# omitted, and every pre-tenancy login looks like this - so it is the bridge case.
NO_ORG_EMAIL = "no-org@tenancy.test"
NO_ORG_PASSWORD = "no-org-pass-1234"


def login(email, password):
    client = appmod.app.test_client()
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return client


def owner_login():
    return login("admin@example.com", "admin123")


def cleanup():
    """Remove anything a previous run left behind, in dependency order."""
    for domain in DOMAINS:
        existing = sites_service.get_site_by_domain(domain)
        if existing:
            sites_service.delete_site(existing["id"])
            print("cleaned up leftover site", domain)

    with appmod.get_connection() as conn:
        for spec in (ALPHA, BETA):
            org = orgs_service.get_org_by_slug(spec["slug"])
            if not org:
                continue
            print("cleaned up leftover org", spec["slug"])
            org_id = org["id"]
            # Bots and jobs first, then the people, then the client itself.
            conn.execute(
                "DELETE FROM messages WHERE bot_id IN (SELECT id FROM bots WHERE org_id = ?)",
                (org_id,),
            )
            conn.execute(
                "DELETE FROM channels WHERE bot_id IN (SELECT id FROM bots WHERE org_id = ?)",
                (org_id,),
            )
            conn.execute("DELETE FROM bots WHERE org_id = ?", (org_id,))
            # Approvals reference a job, so they go before the jobs do.
            conn.execute(
                "DELETE FROM action_approvals WHERE job_id IN "
                "(SELECT id FROM jobs WHERE org_id = ?)",
                (org_id,),
            )
            conn.execute(
                "DELETE FROM job_events WHERE job_id IN (SELECT id FROM jobs WHERE org_id = ?)",
                (org_id,),
            )
            conn.execute("DELETE FROM jobs WHERE org_id = ?", (org_id,))
            conn.execute("DELETE FROM users WHERE org_id = ?", (org_id,))
            conn.execute("DELETE FROM orgs WHERE id = ?", (org_id,))
        conn.execute("DELETE FROM users WHERE email = ?", (NO_ORG_EMAIL,))
def setup_clients():
    """Create both clients, each with its own admin login."""
    alpha = orgs_service.create_org(ALPHA)
    beta = orgs_service.create_org(BETA)
    assert alpha and beta, "could not create the test clients"
    print("client A: org %d (%s) as %s" % (alpha["id"], alpha["slug"], ALPHA["email"]))
    print("client B: org %d (%s) as %s" % (beta["id"], beta["slug"], BETA["email"]))
    return alpha, beta


def test_sites_are_isolated(alpha, beta):
    a = login(ALPHA["email"], ALPHA["password"])
    b = login(BETA["email"], BETA["password"])

    created = a.post("/api/sites", json={"domain": DOMAINS[0], "target": "local"})
    assert created.status_code == 201, created.get_data(as_text=True)
    site = created.get_json()["site"]
    assert site["org_id"] == alpha["id"], "the site was not tagged with A's org: %s" % site
    print("A created site %d (%s) owned by org %s" % (site["id"], site["domain"], site["org_id"]))

    # A sees its own site...
    a_ids = [s["id"] for s in a.get("/api/sites").get_json()["sites"]]
    assert site["id"] in a_ids, "A cannot see the site it just created"

    # ...and B's list contains nothing of A's, nor anything outside B's own org.
    b_sites = b.get("/api/sites").get_json()["sites"]
    assert not any(s["id"] == site["id"] for s in b_sites), "B can see A's site in its list!"
    assert all(s["org_id"] == beta["id"] for s in b_sites), b_sites
    print("B's site list holds %d entries, none of them A's" % len(b_sites))

    # Read, write and delete by id all answer 404. A 403 would confirm it exists.
    attempts = (
        ("get", "/api/sites/%d" % site["id"], None),
        ("put", "/api/sites/%d" % site["id"], {"domain": "stolen.tenancy.test"}),
        ("delete", "/api/sites/%d" % site["id"], None),
        ("get", "/api/sites/%d/dns" % site["id"], None),
        ("post", "/api/sites/%d/check" % site["id"], None),
        ("post", "/api/sites/%d/provision" % site["id"], None),
    )
    for method, path, body in attempts:
        call = getattr(b, method)
        resp = call(path, json=body) if body is not None else call(path)
        assert resp.status_code == 404, "%s %s -> %d" % (method.upper(), path, resp.status_code)
        print("  blocked %-6s %-32s -> 404" % (method.upper(), path))

    # Nothing above may have actually changed the row.
    still = sites_service.get_site(site["id"])
    assert still and still["org_id"] == alpha["id"], "B modified A's site!"
    assert still["domain"] == DOMAINS[0], still
    print("A's site survived every attempt unchanged:", still["domain"])
    return site


def test_bots_and_jobs_are_isolated(alpha, beta):
    a = login(ALPHA["email"], ALPHA["password"])
    b = login(BETA["email"], BETA["password"])

    created = a.post("/api/bots", json={"name": "Alpha Assistant", "welcome_message": "hi"})
    assert created.status_code == 201, created.get_data(as_text=True)
    bot = created.get_json()["bot"]
    assert bot["org_id"] == alpha["id"], bot
    print("A created bot %s owned by org %s" % (bot["id"], bot["org_id"]))

    b_bots = b.get("/api/bots").get_json()["bots"]
    assert not any(x["id"] == bot["id"] for x in b_bots), "B can see A's bot!"
    print("B's bot list holds %d entries, none of them A's" % len(b_bots))

    for method, path, body in (
        ("get", "/api/bots/%s" % bot["id"], None),
        ("put", "/api/bots/%s" % bot["id"], {"name": "Hijacked"}),
        ("delete", "/api/bots/%s" % bot["id"], None),
        ("get", "/api/bots/%s/stats" % bot["id"], None),
        ("get", "/api/bots/%s/conversations" % bot["id"], None),
        ("get", "/api/bots/%s/channels" % bot["id"], None),
    ):
        call = getattr(b, method)
        resp = call(path, json=body) if body is not None else call(path)
        assert resp.status_code == 404, "%s %s -> %d" % (method.upper(), path, resp.status_code)
        print("  blocked %-6s %-38s -> 404" % (method.upper(), path))

    # The widget's endpoint stays public - it is addressed by an opaque bot id,
    # and that is how an embedded widget on a client's site reaches its assistant.
    assert b.get("/api/bots/%s/public" % bot["id"]).status_code == 200
    print("the widget's public endpoint is still reachable (by design)")

    # A real run, then the same cross-tenant attempts against it.
    resp = a.post("/api/jobs", json={"prompt": "tenancy echo", "task": "echo"})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    job = resp.get_json()["job"]
    assert job["org_id"] == alpha["id"], job
    print("A started job %d owned by org %s" % (job["id"], job["org_id"]))

    b_jobs = b.get("/api/jobs").get_json()["jobs"]
    assert not any(x["id"] == job["id"] for x in b_jobs), "B can see A's job!"
    print("B's job list holds %d entries, none of them A's" % len(b_jobs))

    for method, path in (
        ("get", "/api/jobs/%d" % job["id"]),
        ("get", "/api/jobs/%d/events" % job["id"]),
        ("post", "/api/jobs/%d/cancel" % job["id"]),
    ):
        resp = getattr(b, method)(path)
        assert resp.status_code == 404, "%s %s -> %d" % (method.upper(), path, resp.status_code)
        print("  blocked %-6s %-32s -> 404" % (method.upper(), path))
    return bot["id"], job["id"]


def test_sse_stream_is_isolated(job_id):
    """The live transcript is served by FastAPI, so its tenant check is its own.

    This goes through the ASGI app rather than the Flask test client, because the
    Flask app has no such route - testing it there would pass without proving
    anything.
    """
    from fastapi.testclient import TestClient

    import asgi

    client = TestClient(asgi.app)
    resp = client.post("/api/auth/login", json={
        "email": BETA["email"], "password": BETA["password"],
    })
    assert resp.status_code == 200, resp.text
    streamed = client.get("/api/jobs/%d/stream" % job_id)
    assert streamed.status_code == 404, \
        "the SSE stream leaked a transcript across clients: %d" % streamed.status_code
    print("  blocked GET    /api/jobs/%d/stream -> 404  (FastAPI)" % job_id)


def test_approvals_are_isolated(alpha, beta, job_id):
    """An approval is a permission to write to a client's server, so it is theirs."""
    import actions

    approval_id = actions.request_approval(
        "write_site_page", "tenancy check", {"path": "/index.html"}, job_id=job_id,
    )
    print("A's job %d raised approval %d" % (job_id, approval_id))

    a = login(ALPHA["email"], ALPHA["password"])
    b = login(BETA["email"], BETA["password"])

    assert any(x["id"] == approval_id for x in a.get("/api/approvals").get_json()["approvals"]), \
        "A cannot see its own run's approval"
    b_approvals = b.get("/api/approvals").get_json()["approvals"]
    assert not any(x["id"] == approval_id for x in b_approvals), "B can see A's approval!"
    print("B's approval list holds %d entries, none of them A's" % len(b_approvals))

    for decision in ("approve", "deny"):
        resp = b.post("/api/approvals/%d/%s" % (approval_id, decision))
        assert resp.status_code == 404, \
            "B could %s another client's approval: %d" % (decision, resp.status_code)
        print("  blocked POST   /api/approvals/%d/%-8s -> 404" % (approval_id, decision))

    # It must still be pending - B's attempts may not have decided it.
    assert actions.get_approval(approval_id)["status"] == "pending", \
        "B changed the approval's state!"
    print("A's approval is still pending")

    # A can decide its own.
    assert a.post("/api/approvals/%d/deny" % approval_id).status_code == 200
    assert actions.get_approval(approval_id)["status"] == "denied"
    print("A decided its own approval -> denied")
    return approval_id


def test_user_without_an_org_sees_nothing(site, bot_id, job_id):
    """A login with no org belongs to no client, so it must reach no client's data.

    The Users tab produces these whenever org_id is omitted, and every pre-tenancy
    login is one - so this guards the migration path, not just a corner case.
    """
    created = owner_login().post("/api/users", json={
        "email": NO_ORG_EMAIL, "name": "No Org", "password": NO_ORG_PASSWORD,
        "role": "member",
    })
    assert created.status_code == 201, created.get_data(as_text=True)
    assert created.get_json()["user"]["org_id"] is None, created.get_json()

    nobody = login(NO_ORG_EMAIL, NO_ORG_PASSWORD)
    for path, key in (("/api/sites", "sites"), ("/api/bots", "bots"), ("/api/jobs", "jobs")):
        rows = nobody.get(path).get_json()[key]
        assert rows == [], "%s leaked %d row(s) to a login with no org" % (path, len(rows))
        print("  %-12s -> 0 rows" % path)

    # ...nor by id, even though those rows exist and are perfectly readable.
    for path in ("/api/sites/%d" % site["id"], "/api/bots/%s" % bot_id,
                 "/api/jobs/%d" % job_id, "/api/jobs/%d/events" % job_id):
        assert nobody.get(path).status_code == 404, path
    print("  and every client row is 404 when asked for by id")


def test_owner_only_surfaces_are_shut(alpha):
    """The console surfaces a client must never reach."""
    a = login(ALPHA["email"], ALPHA["password"])
    for path in ("/api/settings", "/api/ssh-hosts", "/api/agents"):
        resp = a.get(path)
        assert resp.status_code == 403, "GET %s -> %d (expected 403)" % (path, resp.status_code)
        print("  blocked GET    %-18s -> 403" % path)

    # The hosting fleet's SSH targets must not even appear in a client's payload.
    body = a.get("/api/sites").get_json()
    assert body["targets"] == [{"value": "local", "label": "This server (local)"}], body["targets"]
    assert "server_ip" in body, "a client still needs the IP to point its DNS at"
    print("  a client sees only the local deploy target, plus the DNS IP")

    # Nor may a client admin delete the platform operator's account.
    owner_id = owner_login().get("/api/auth/me").get_json()["user"]["id"]
    resp = a.delete("/api/users/%d" % owner_id)
    assert resp.status_code == 403, resp.status_code
    print("  blocked DELETE /api/users/%-8d -> 403  (the platform owner)" % owner_id)

    # A client's team list contains nobody from another client.
    users = a.get("/api/users").get_json()["users"]
    assert users, "A's team list should contain A's own admin"
    assert all(u["org_id"] == alpha["id"] for u in users), users
    print("  A's team list holds %d user(s), all inside A's own org" % len(users))


def test_owner_still_sees_every_client(site):
    """The operator keeps the cross-client view, or the console is useless."""
    owner = owner_login()
    ids = [s["id"] for s in owner.get("/api/sites").get_json()["sites"]]
    assert site["id"] in ids, "the platform owner cannot see a client's site"
    for path in ("/api/bots", "/api/ssh-hosts", "/api/agents", "/api/settings"):
        assert owner.get(path).status_code == 200, path
    print("owner sees %d site(s) across %d client(s)" % (len(ids), orgs_service.count_orgs()))

    # ...and can act on any client's site, which is what onboarding needs.
    assert owner.get("/api/sites/%d" % site["id"]).status_code == 200
    me = owner.get("/api/auth/me").get_json()["user"]
    assert me["is_owner"] is True and me["org"] is None, me
    print("owner /api/auth/me: is_owner=%s, org=%s" % (me["is_owner"], me["org"]))


def main():
    cleanup()
    alpha, beta = setup_clients()
    try:
        print("\n--- sites ---")
        site = test_sites_are_isolated(alpha, beta)
        print("\n--- bots and jobs ---")
        bot_id, job_id = test_bots_and_jobs_are_isolated(alpha, beta)
        print("\n--- live transcript ---")
        test_sse_stream_is_isolated(job_id)
        print("\n--- approvals ---")
        test_approvals_are_isolated(alpha, beta, job_id)
        print("\n--- a login with no org ---")
        test_user_without_an_org_sees_nothing(site, bot_id, job_id)
        print("\n--- console surfaces, closed to a client ---")
        test_owner_only_surfaces_are_shut(alpha)
        print("\n--- the platform operator's view ---")
        test_owner_still_sees_every_client(site)
    finally:
        # Always leave the database as we found it, even when an assertion fails.
        cleanup()
    print("\nTENANCY OK")


if __name__ == "__main__":
    main()


