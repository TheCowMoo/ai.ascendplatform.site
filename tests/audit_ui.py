"""Every page's endpoints, exercised against a real client with real rows.

Run: .\\venv\\Scripts\\python.exe tests\\audit_ui.py
     .\\venv\\Scripts\\python.exe tests\\audit_ui.py --keep   (leave the fixtures behind)

A page is broken when the endpoint behind it answers 404/500 or omits a key the view
dereferences - and that is invisible in a unit test of the service behind it. So this
builds one client with a site, a bot, a job, a draft of every kind and an automation, then
walks the console's reads twice: once as the platform owner acting as that client, once as
the client. Owner-only endpoints must answer 403 to the client, never 500.

Fixtures are named "Audit Fixture ..." and removed at the end.
"""

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.pop("ALLOW_LIVE_ACTIONS", None)

import app as appmod  # noqa: E402
import automations  # noqa: E402
import db  # noqa: E402
import deliverables  # noqa: E402
import jobs  # noqa: E402
import orgs as orgs_service  # noqa: E402
import sites as sites_service  # noqa: E402

SYSTEM = {
    "name": "Audit Fixture Client", "slug": "audit-fixture",
    "email": "audit@fixture.test", "password": "audit-pass-1234",
}
DOMAIN = "audit-fixture.test"
KINDS = ("blog", "funnel", "email", "website")
KEEP = "--keep" in sys.argv


def login(email, password):
    client = appmod.app.test_client()
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return client


def cleanup():
    with db.get_connection() as conn:
        org = conn.execute("SELECT id FROM orgs WHERE slug = ?", (SYSTEM["slug"],)).fetchone()
        if not org:
            return
        org_id = org["id"]
        conn.execute("DELETE FROM messages WHERE bot_id IN "
                     "(SELECT id FROM bots WHERE org_id = ?)", (org_id,))
        conn.execute("DELETE FROM channels WHERE bot_id IN "
                     "(SELECT id FROM bots WHERE org_id = ?)", (org_id,))
        conn.execute("DELETE FROM bots WHERE org_id = ?", (org_id,))
        conn.execute("DELETE FROM job_events WHERE job_id IN "
                     "(SELECT id FROM jobs WHERE org_id = ?)", (org_id,))
        conn.execute("DELETE FROM jobs WHERE org_id = ?", (org_id,))
        for table in ("automations", "automation_runs", "deliverables", "site_versions",
                      "sites", "users", "client_settings", "business_profiles",
                      "data_sources", "invites", "site_snapshots"):
            try:
                conn.execute("DELETE FROM %s WHERE org_id = ?" % table, (org_id,))
            except Exception:
                pass    # a table this build does not have yet is not an audit failure
        conn.execute("DELETE FROM orgs WHERE id = ?", (org_id,))


def build_fixtures():
    cleanup()
    org = orgs_service.create_org(SYSTEM)
    assert org, "could not create the fixture client"
    client = login(SYSTEM["email"], SYSTEM["password"])

    bot = client.post("/api/bots", json={"name": "Audit Fixture Bot"})
    site = sites_service.create_site({"domain": DOMAIN, "target": "local"}, org_id=org["id"])
    job_id = jobs.create_job("Audit Fixture job", "write a launch email", None,
                             "selector", "agent_team", org["id"])
    drafts = {}
    for kind in KINDS:
        item = deliverables.create_deliverable(
            {"title": "Audit Fixture %s" % kind, "kind": kind,
             "content": "<html><body><p>%s</p></body></html>" % kind},
            org_id=org["id"],
        )
        drafts[kind] = item
    auto = automations.create(org["id"], "Audit Fixture automation", [
        {"id": "draft", "type": "agent", "title": "Draft it",
         "config": {"prompt": "Write an email."}, "next": ["report"]},
        {"id": "report", "type": "notify", "title": "Say done", "next": ["end"]},
        {"id": "end", "type": "stop", "title": "Done", "next": []},
    ])
    print("fixtures: org %d, site %s, bot %s, job %d, %d drafts, automation %s" % (
        org["id"], "ok" if site else "FAILED",
        "ok" if bot.status_code == 201 else bot.status_code, job_id, len(drafts),
        auto or "FAILED"))
    return org, client, bot, site, job_id, drafts, auto


def checks(org, bot, site, job_id, drafts, auto):
    """(page, path, keys the view dereferences, what a client may get).

    ``keys`` is the contract between the endpoint and the render code: a missing key is a
    blank panel, not an error the console can show.
    """
    bot_id = (bot.get_json() or {}).get("bot", {}).get("id") if bot.status_code == 201 else None
    rows = [
        ("Dashboard    ", "/api/bots", ["bots"], "ok"),
        ("Dashboard    ", "/api/jobs", ["jobs"], "ok"),
        ("Dashboard    ", "/api/profile", ["profile"], "ok"),
        ("Analytics    ", "/api/analytics/summary?days=7", ["current", "change"], "ok"),
        ("Automations  ", "/api/automations", ["automations"], "ok"),
        ("Automations  ", "/api/automations/%d" % auto, ["automation"], "ok"),
        ("Bots         ", "/api/bots", ["bots"], "ok"),
        ("Editor       ", "/api/bots/%s" % bot_id, ["bot"], "ok"),
        ("Stats        ", "/api/bots/%s/stats" % bot_id, [], "ok"),
        ("Conversations", "/api/bots/%s/conversations" % bot_id, [], "ok"),
        ("Blogs        ", "/api/deliverables?kind=blog", ["deliverables"], "ok"),
        ("Funnels      ", "/api/deliverables?kind=funnel", ["deliverables"], "ok"),
        ("Emails       ", "/api/deliverables?kind=email", ["deliverables"], "ok"),
        ("Library      ", "/api/deliverables", ["deliverables"], "ok"),
        ("Preview      ", "/api/deliverables/%d" % drafts["email"]["id"], ["deliverable"], "ok"),
        ("Jobs         ", "/api/jobs", ["jobs"], "ok"),
        ("Job          ", "/api/jobs/%d" % job_id, ["job"], "ok"),
        ("Job          ", "/api/jobs/%d/events" % job_id, ["events"], "ok"),
        ("Websites     ", "/api/sites", ["sites"], "ok"),
        ("Websites     ", "/api/sites/%d" % site["id"], ["site"], "ok"),
        ("Websites     ", "/api/sites/%d/versions" % site["id"], [], "ok"),
        ("Domains      ", "/api/sites/%d/dns-report" % site["id"], ["report", "records"], "ok"),
        ("Profile      ", "/api/profile", ["profile"], "ok"),
        ("Profile      ", "/api/profile/knowledge", [], "ok"),
        ("Settings     ", "/api/settings", [], "ok"),
        ("Team         ", "/api/users", ["users"], "ok"),
        ("Approvals    ", "/api/approvals?status=pending", [], "ok"),
        ("Me           ", "/api/auth/me", ["user"], "ok"),
        ("Build        ", "/api/version", ["commit", "started_at"], "ok"),
        ("Workspace    ", "/api/session/org", ["acting_org", "orgs"], "ok"),
        # Platform area: the client must be refused, and refused with 403 rather than 500.
        ("Platform     ", "/api/orgs", ["orgs"], "deny"),
        ("Platform     ", "/api/orgs/%d" % org["id"], [], "deny"),
        ("Platform     ", "/api/platform/email", [], "deny"),
        ("Platform     ", "/api/ssh-hosts", [], "deny"),
        ("Platform     ", "/api/agents", ["agents"], "deny"),
        ("Platform     ", "/api/data-sources", [], "ok"),   # client_admin: own org only
        ("Platform     ", "/api/providers", [], "deny"),
    ]
    return rows


def walk(persona, client, rows):
    as_client = persona == "client"
    print("\n== as %s ==" % ("the client" if as_client else "the owner acting as the client"))
    failures = []
    for page, path, keys, client_rule in rows:
        try:
            resp = client.get(path)
            code = resp.status_code
        except Exception as exc:
            failures.append((page, path, "EXC %s" % exc, ""))
            print("  %s %-38s EXC  %s" % (page, path, exc))
            continue
        data = resp.get_json(silent=True)
        data = data if isinstance(data, dict) else {}
        missing = [k for k in keys if k not in data]
        if client_rule == "deny" and as_client:
            good = code == 403
            note = "" if good else "expected 403 for a client"
        else:
            good = code == 200 and not missing
            note = ("" if code == 200 else "status") + (" missing " + ",".join(missing) if missing else "")
        print("  %s %-38s %-4s %s" % (page, path, code, "" if good else "<<< " + note))
        if not good:
            failures.append((page, path, code, note))
    return failures


def main():
    org, client, bot, site, job_id, drafts, auto = build_fixtures()
    rows = checks(org, bot, site, job_id, drafts, auto)
    failures = []

    owner = login("admin@example.com", "admin123")
    owner.post("/api/session/org", json={"org_id": org["id"]})
    failures += walk("owner", owner, rows)

    client_session = login(SYSTEM["email"], SYSTEM["password"])
    failures += walk("client", client_session, rows)

    owner.post("/api/session/org", json={"org_id": None})
    if KEEP:
        print("\n--keep: fixtures left in place (org %d)" % org["id"])
    else:
        cleanup()

    print("\n%d check(s), %d failure(s)" % (len(rows) * 2, len(failures)))
    if failures:
        for page, path, code, note in failures:
            print("  FAIL %-13s %-38s %s %s" % (page.strip(), path, code, note))
        return 1
    print("UI AUDIT OK - every page's endpoint answers, in both roles")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        if not KEEP:
            cleanup()
        sys.exit(1)

