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

# A second login at client A, to prove the brand is shared rather than per user.
STAFF_EMAIL = "staff@tenancy.test"
STAFF_PASSWORD = "staff-pass-1234"

# A client created through the platform console during the run.
GAMMA = {
    "name": "Tenancy Gamma", "slug": "tenancy-gamma",
    "email": "gamma@tenancy.test", "password": "gamma-pass-1234",
}


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
        for spec in (ALPHA, BETA, GAMMA):
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
            conn.execute("DELETE FROM client_settings WHERE org_id = ?", (org_id,))
            conn.execute("DELETE FROM deliverables WHERE org_id = ?", (org_id,))
            conn.execute("DELETE FROM business_profiles WHERE org_id = ?", (org_id,))
            conn.execute("DELETE FROM orgs WHERE id = ?", (org_id,))
        conn.execute("DELETE FROM users WHERE email = ?", (NO_ORG_EMAIL,))
        conn.execute("DELETE FROM users WHERE email = ?", (STAFF_EMAIL,))
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


def test_profiles_are_per_client(alpha, beta):
    """The brand belongs to the client, so its staff share one profile."""
    a = login(ALPHA["email"], ALPHA["password"])
    b = login(BETA["email"], BETA["password"])

    saved = a.put("/api/profile", json={"name": "Alpha Co", "brand_color": "#123456"})
    assert saved.status_code == 200, saved.get_data(as_text=True)
    prof = saved.get_json()["profile"]
    assert prof["org_id"] == alpha["id"], prof
    print("A saved its profile: %r (org %s)" % (prof["name"], prof["org_id"]))

    # B's profile is its own - not A's, and not the platform's.
    b_prof = b.get("/api/profile").get_json()["profile"]
    assert b_prof["org_id"] == beta["id"], b_prof
    assert b_prof["name"] != "Alpha Co", b_prof
    print("B's profile is its own: %r (org %s)" % (b_prof["name"], b_prof["org_id"]))

    # The platform's brand is org 0, and A's edit left it alone.
    owner = owner_login().get("/api/profile").get_json()["profile"]
    assert owner["org_id"] == 0, owner
    print("the platform's profile is org 0: %r (untouched)" % owner["name"])

    # A second login at A shares the profile rather than getting its own - the
    # behaviour this replaced created one profile per user.
    created = owner_login().post("/api/users", json={
        "email": STAFF_EMAIL, "name": "Alpha Staff", "password": STAFF_PASSWORD,
        "role": "member", "org_id": alpha["id"],
    })
    assert created.status_code == 201, created.get_data(as_text=True)
    staff_id = created.get_json()["user"]["id"]
    staff_prof = login(STAFF_EMAIL, STAFF_PASSWORD).get("/api/profile").get_json()["profile"]
    assert staff_prof["org_id"] == alpha["id"], staff_prof
    assert staff_prof["name"] == "Alpha Co", staff_prof
    print("a second login at A sees the same profile: %r" % staff_prof["name"])
    return staff_id


def test_passwords(alpha, staff_id):
    """Your own password needs the current one; an admin resets their own team's."""
    staff = login(STAFF_EMAIL, STAFF_PASSWORD)

    wrong = staff.post("/api/auth/password", json={
        "current_password": "not-it", "new_password": "a-long-enough-one",
    })
    assert wrong.status_code == 403, wrong.status_code
    short = staff.post("/api/auth/password", json={
        "current_password": STAFF_PASSWORD, "new_password": "short",
    })
    assert short.status_code == 400, short.status_code
    print("  a wrong current password -> 403, a too-short new one -> 400")

    changed = staff.post("/api/auth/password", json={
        "current_password": STAFF_PASSWORD, "new_password": "staff-pass-9876",
    })
    assert changed.status_code == 200, changed.get_data(as_text=True)
    login(STAFF_EMAIL, "staff-pass-9876")
    print("  the member changed it and signed in with the new one")

    stale = appmod.app.test_client().post("/api/auth/login", json={
        "email": STAFF_EMAIL, "password": STAFF_PASSWORD,
    })
    assert stale.status_code == 401, stale.status_code
    print("  the old password is refused -> 401")

    # A client admin may reset their own team's password...
    a = login(ALPHA["email"], ALPHA["password"])
    reset = a.post("/api/users/%d/password" % staff_id, json={"new_password": "reset-by-alpha-1"})
    assert reset.status_code == 200, reset.get_data(as_text=True)
    login(STAFF_EMAIL, "reset-by-alpha-1")
    print("  A's admin reset its own member's password -> 200")

    # ...but neither another client's user nor a platform operator.
    b_id = login(BETA["email"], BETA["password"]).get("/api/auth/me").get_json()["user"]["id"]
    blocked = a.post("/api/users/%d/password" % b_id, json={"new_password": "hijacked-pass-1"})
    assert blocked.status_code == 403, blocked.status_code
    owner_id = owner_login().get("/api/auth/me").get_json()["user"]["id"]
    blocked = a.post("/api/users/%d/password" % owner_id, json={"new_password": "hijacked-pass-1"})
    assert blocked.status_code == 403, blocked.status_code
    print("  blocked resetting another client's user and the owner -> 403")


def test_settings_are_scoped(alpha, beta):
    """A client configures their own SMTP and can never see the platform's keys."""
    import settings as settings_mod

    a = login(ALPHA["email"], ALPHA["password"])
    b = login(BETA["email"], BETA["password"])

    saved = a.put("/api/settings", json={"values": {
        "SMTP_HOST": "smtp.alpha.test", "SMTP_FROM": "hi@alpha.test",
    }})
    assert saved.status_code == 200, saved.get_data(as_text=True)
    assert "SMTP_HOST" in saved.get_json()["changed"], saved.get_json()
    print("A saved its own SMTP: %s" % saved.get_json()["changed"])

    # The value is A's alone: not B's, and not the platform's.
    assert settings_mod.get("SMTP_HOST", org_id=alpha["id"]) == "smtp.alpha.test"
    assert settings_mod.get("SMTP_HOST", org_id=beta["id"]) != "smtp.alpha.test"
    assert settings_mod.get("SMTP_HOST") != "smtp.alpha.test", \
        "a client's value leaked into the platform scope"
    print("  resolution: A -> %r, B -> %r, platform -> %r" % (
        settings_mod.get("SMTP_HOST", org_id=alpha["id"]),
        settings_mod.get("SMTP_HOST", org_id=beta["id"]),
        settings_mod.get("SMTP_HOST"),
    ))

    # B's panel must not carry A's value, and one client's history is not another's.
    b_fields = {f["key"]: f for g in b.get("/api/settings").get_json()["groups"]
                for f in g["fields"]}
    assert b_fields["SMTP_HOST"].get("value") != "smtp.alpha.test", b_fields["SMTP_HOST"]
    a_keys = set(h["key"] for h in a.get("/api/settings").get_json()["history"])
    b_keys = set(h["key"] for h in b.get("/api/settings").get_json()["history"])
    assert "SMTP_HOST" in a_keys, a_keys
    assert "SMTP_HOST" not in b_keys, b_keys
    print("  audit trail: A sees %s, B sees %s" % (sorted(a_keys), sorted(b_keys)))

    # A may only test client-scoped groups: the platform's Pexels check spends the
    # operator's key and would confirm to the client that it exists.
    resp = a.post("/api/settings/test/pexels")
    assert resp.status_code == 200 and resp.get_json()["ok"] is False, resp.get_json()
    print("  A cannot test the platform's Pexels group: %s" % resp.get_json()["output"][:44])

    # Leave nothing behind, so a second run starts from an empty SMTP config.
    cleared = a.put("/api/settings", json={"clear": ["SMTP_HOST", "SMTP_FROM"]})
    assert cleared.status_code == 200, cleared.get_data(as_text=True)
    assert settings_mod.get("SMTP_HOST", org_id=alpha["id"]) != "smtp.alpha.test"
    print("  cleared A's SMTP again: %s" % cleared.get_json()["changed"])


def test_a_clients_mail_settings_are_used(alpha, job_id):
    """The stored SMTP is not just for display: a job sends through its client's."""
    import tools.mail as mail

    a = login(ALPHA["email"], ALPHA["password"])
    a.put("/api/settings", json={"values": {"SMTP_HOST": "smtp.alpha.test"}})

    resolved = mail.org_of(job_id)
    assert resolved == alpha["id"], resolved
    assert mail.host(resolved) == "smtp.alpha.test", mail.host(resolved)
    print("the mailer resolves the job's client: org %s -> %s" % (resolved, mail.host(resolved)))

    # A send with no job, or another client's, does not pick up A's server.
    assert mail.host(mail.org_of(None)) != "smtp.alpha.test"
    assert mail.host(999999) != "smtp.alpha.test"
    print("  a send with no job falls back to the platform's SMTP")

    a.put("/api/settings", json={"clear": ["SMTP_HOST"]})


def test_platform_console(alpha):
    """The console onboards a client; no client can reach the console itself."""
    owner = owner_login()

    created = owner.post("/api/orgs", json={
        "name": GAMMA["name"], "email": GAMMA["email"], "password": GAMMA["password"],
    })
    assert created.status_code == 201, created.get_data(as_text=True)
    gamma = created.get_json()["org"]
    assert gamma["slug"] == GAMMA["slug"], gamma
    print("owner created client %d (%s)" % (gamma["id"], gamma["slug"]))

    # The first admin is created in the same call, so the client can sign in at once.
    login(GAMMA["email"], GAMMA["password"])
    print("  its first admin signed in straight away")

    # Plan and status are data only - which is exactly what payment wiring will move.
    updated = owner.put("/api/orgs/%d" % gamma["id"], json={"plan": "pro", "status": "suspended"})
    assert updated.status_code == 200, updated.get_data(as_text=True)
    assert updated.get_json()["org"]["plan"] == "pro", updated.get_json()
    print("  plan/status edited -> pro/suspended")

    # A client admin is refused every console route, read or write.
    a = login(ALPHA["email"], ALPHA["password"])
    for method, path in (("get", "/api/orgs"),
                         ("post", "/api/orgs"),
                         ("put", "/api/orgs/%d" % gamma["id"]),
                         ("delete", "/api/orgs/%d" % gamma["id"])):
        call = getattr(a, method)
        resp = call(path) if method == "get" else call(path, json={"name": "hijack"})
        assert resp.status_code == 403, "%s %s -> %d" % (method.upper(), path, resp.status_code)
    print("  a client admin is refused every /api/orgs route -> 403")

    # A client with logins cannot be deleted out from under them.
    blocked = owner.delete("/api/orgs/%d" % gamma["id"])
    assert blocked.status_code == 409, blocked.status_code
    print("  deleting a client that still has logins -> 409")


def test_deliverables_are_isolated(alpha, beta):
    """Blogs, funnels, emails and the library are the client's content."""
    a = login(ALPHA["email"], ALPHA["password"])
    b = login(BETA["email"], BETA["password"])

    created = a.post("/api/deliverables", json={
        "kind": "blog", "title": "Alpha post", "summary": "A first draft",
    })
    assert created.status_code == 201, created.get_data(as_text=True)
    item = created.get_json()["deliverable"]
    assert item["org_id"] == alpha["id"], item
    assert item["slug"] == "alpha-post", item
    print("A created a blog draft: %r (org %s)" % (item["title"], item["org_id"]))

    blogs = a.get("/api/deliverables?kind=blog").get_json()
    assert any(x["id"] == item["id"] for x in blogs["deliverables"]), blogs
    assert blogs["counts"]["blog"] >= 1, blogs["counts"]
    print("  A's Blogs tab: %d item(s), counts %s" % (len(blogs["deliverables"]), blogs["counts"]))

    b_all = b.get("/api/deliverables").get_json()["deliverables"]
    assert not any(x["id"] == item["id"] for x in b_all), "B can see A's content!"
    print("  B's Library: %d item(s), none of them A's" % len(b_all))

    for method, path, body in (
        ("get", "/api/deliverables/%d" % item["id"], None),
        ("put", "/api/deliverables/%d" % item["id"], {"title": "Hijacked"}),
        ("delete", "/api/deliverables/%d" % item["id"], None),
    ):
        call = getattr(b, method)
        resp = call(path, json=body) if body is not None else call(path)
        assert resp.status_code == 404, "%s -> %d" % (path, resp.status_code)
        print("  blocked %-6s %-26s -> 404" % (method.upper(), path))

    # Publishing is a status change, and it survives the round trip.
    published = a.put("/api/deliverables/%d" % item["id"], json={
        "status": "published", "url": "https://alpha.test/blog/alpha-post",
    })
    assert published.status_code == 200, published.get_data(as_text=True)
    assert published.get_json()["deliverable"]["status"] == "published"
    print("  A published it -> %s" % published.get_json()["deliverable"]["url"])

    # A bad kind is rejected rather than quietly stored.
    assert a.get("/api/deliverables?kind=sausages").status_code == 400
    assert a.delete("/api/deliverables/%d" % item["id"]).status_code == 200
    print("  an unknown kind -> 400; A deleted its own draft -> 200")


def test_owner_only_surfaces_are_shut(alpha):
    """The console surfaces a client must never reach."""
    a = login(ALPHA["email"], ALPHA["password"])
    for path in ("/api/ssh-hosts", "/api/agents"):
        resp = a.get(path)
        assert resp.status_code == 403, "GET %s -> %d (expected 403)" % (path, resp.status_code)
        print("  blocked GET    %-18s -> 403" % path)

    # Settings are reachable, but only the client's own scope: a client admin may
    # configure their SMTP and must never see the platform's key names at all.
    resp = a.get("/api/settings")
    assert resp.status_code == 200, resp.status_code
    groups = resp.get_json()["groups"]
    assert groups, "a client admin should see their own settings"
    for group in groups:
        assert group["scope"] == "client", group["id"]
    keys = [f["key"] for g in groups for f in g["fields"]]
    assert keys, keys
    for platform_key in ("DEEPSEEK_API_KEY", "PEXELS_API_KEY", "GEMINI_API_KEY"):
        assert platform_key not in keys, "a platform key leaked into a client's panel"
    print("  a client sees %d group(s), client-scoped only: %s" % (len(groups), keys))

    # ...and cannot write a platform key through the same endpoint.
    resp = a.put("/api/settings", json={"values": {"DEEPSEEK_API_KEY": "stolen"}})
    assert resp.status_code == 400, resp.status_code
    print("  blocked PUT  platform key -> 400 (%s)" % resp.get_json()["error"][:52])

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
        print("\n--- profiles per client ---")
        staff_id = test_profiles_are_per_client(alpha, beta)
        print("\n--- passwords ---")
        test_passwords(alpha, staff_id)
        print("\n--- client-scoped settings ---")
        test_settings_are_scoped(alpha, beta)
        print("\n--- a client's own SMTP, used by a job ---")
        test_a_clients_mail_settings_are_used(alpha, job_id)
        print("\n--- platform console: onboarding a client ---")
        test_platform_console(alpha)
        print("\n--- content: blogs, funnels, emails, library ---")
        test_deliverables_are_isolated(alpha, beta)
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


