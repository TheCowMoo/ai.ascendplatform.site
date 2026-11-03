"""The workspace switcher, and the guards that stop unowned work.

Run: .\\venv\\Scripts\\python.exe tests\\smoke_workspace.py

The operator used to have no tenant at all, so anything launched from their session was
written with ``org_id = NULL``: it showed up in their Jobs list and was invisible in every
client's Emails and Funnels tab. Two things fix that, and this file pins both:

* the switcher writes the chosen client into the *session*, so ``current_org()`` and
  ``list_scope()`` follow it - and a client can never set it;
* with nothing chosen, creating work is refused outright rather than saved nowhere.
"""

import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.pop("ALLOW_LIVE_ACTIONS", None)

from autogen_core import CancellationToken  # noqa: E402

import app as appmod  # noqa: E402
import db  # noqa: E402
import deliverables  # noqa: E402
import jobs  # noqa: E402
import orgs as orgs_service  # noqa: E402
import provisioner  # noqa: E402
import scheduler  # noqa: E402
import sites as sites_service  # noqa: E402
import tools as tool_registry  # noqa: E402

DRY = CancellationToken()
TOKEN = uuid.uuid4().hex[:6]

ALPHA = {
    "name": "Workspace Alpha", "slug": "workspace-alpha",
    "email": "alpha@workspace.test", "password": "alpha-pass-1234",
}
BETA = {
    "name": "Workspace Beta", "slug": "workspace-beta",
    "email": "beta@workspace.test", "password": "beta-pass-1234",
}


def login(email, password):
    client = appmod.app.test_client()
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return client


def cleanup():
    with db.get_connection() as conn:
        for spec in (ALPHA, BETA):
            org = orgs_service.get_org_by_slug(spec["slug"])
            if not org:
                continue
            conn.execute("DELETE FROM job_events WHERE job_id IN "
                         "(SELECT id FROM jobs WHERE org_id = ?)", (org["id"],))
            conn.execute("DELETE FROM jobs WHERE org_id = ?", (org["id"],))
            conn.execute("DELETE FROM users WHERE org_id = ?", (org["id"],))
            conn.execute("DELETE FROM deliverables WHERE org_id = ?", (org["id"],))
            conn.execute("DELETE FROM sites WHERE org_id = ?", (org["id"],))
            conn.execute("DELETE FROM orgs WHERE id = ?", (org["id"],))
        # The sweep fixture has no org, so it is not reached by the loop above.
        conn.execute("DELETE FROM sites WHERE domain LIKE ?", ("%%%s%%" % TOKEN,))


def make_clients():
    cleanup()
    alpha = orgs_service.create_org(ALPHA)
    beta = orgs_service.create_org(BETA)
    assert alpha and beta, "could not create the test clients"
    print("client A: org %d   client B: org %d" % (alpha["id"], beta["id"]))
    return alpha, beta


def a_draft(org_id, title):
    """A draft straight in the database: the list endpoints are what is under test."""
    return deliverables.create_deliverable(
        {"title": title, "kind": "email",
         "content": "<html><body><p>hi</p></body></html>"},
        org_id=org_id,
    )


def test_owner_picks_a_client_and_the_lists_follow(alpha, beta):
    owner = login("admin@example.com", "admin123")
    mine = a_draft(alpha["id"], "Alpha only")
    theirs = a_draft(beta["id"], "Beta only")

    state = owner.get("/api/session/org").get_json()
    assert state["acting_org"] is None, state
    ids = [o["id"] for o in state["orgs"]]
    assert alpha["id"] in ids and beta["id"] in ids, ids
    print("operator sees %d clients, acting as nobody" % len(ids))

    # With no client picked the operator keeps the overview.
    all_ids = [d["id"] for d in owner.get("/api/deliverables?kind=email").get_json()["deliverables"]]
    assert mine["id"] in all_ids and theirs["id"] in all_ids

    # Pick A: the list narrows to A.
    set_alpha = owner.post("/api/session/org", json={"org_id": alpha["id"]})
    assert set_alpha.status_code == 200, set_alpha.get_data(as_text=True)
    scoped = owner.get("/api/deliverables?kind=email").get_json()["deliverables"]
    scoped_ids = [d["id"] for d in scoped]
    assert mine["id"] in scoped_ids, "A's draft is missing while acting as A"
    assert theirs["id"] not in scoped_ids, "B's draft leaked into A's view"
    print("acting as A (%d): %d draft(s), none of B's" % (alpha["id"], len(scoped)))

    # Pick B: the mirror image.
    owner.post("/api/session/org", json={"org_id": beta["id"]})
    b_ids = [d["id"] for d in owner.get("/api/deliverables?kind=email").get_json()["deliverables"]]
    assert theirs["id"] in b_ids and mine["id"] not in b_ids
    print("acting as B (%d): %d draft(s), none of A's" % (beta["id"], len(b_ids)))

    # Back to the overview.
    owner.post("/api/session/org", json={"org_id": None})
    again = [d["id"] for d in owner.get("/api/deliverables?kind=email").get_json()["deliverables"]]
    assert mine["id"] in again and theirs["id"] in again
    print("cleared: both drafts visible again")


def test_only_a_real_client_id_is_accepted():
    owner = login("admin@example.com", "admin123")
    bad = owner.post("/api/session/org", json={"org_id": "not-a-number"})
    assert bad.status_code == 400, bad.get_data(as_text=True)
    missing = owner.post("/api/session/org", json={"org_id": 999999})
    assert missing.status_code == 404, missing.get_data(as_text=True)
    print("junk and unknown ids refused")


def test_a_client_cannot_switch_clients(alpha, beta):
    a = login(ALPHA["email"], ALPHA["password"])
    state = a.get("/api/session/org").get_json()
    assert state["acting_org"] == alpha["id"], state
    assert state["orgs"] == [], "a client was handed the list of other tenants"
    print("client A sees only its own name: %s" % state["name"])

    resp = a.post("/api/session/org", json={"org_id": beta["id"]})
    assert resp.status_code == 403, resp.get_data(as_text=True)
    after = a.get("/api/session/org").get_json()["acting_org"]
    assert after == alpha["id"], "A's tenant changed despite the 403"
    print("client A's attempt to act as B -> 403, tenant unchanged")


def test_a_fresh_login_starts_at_all_clients(alpha):
    """Nobody should inherit the last session's client just by signing in."""
    owner = login("admin@example.com", "admin123")
    owner.post("/api/session/org", json={"org_id": alpha["id"]})
    assert owner.get("/api/session/org").get_json()["acting_org"] == alpha["id"]
    # The Studio keys its remembered chat and draft by this value, so /me has to carry it -
    # otherwise the operator's one login shares a slot across every client.
    me = owner.get("/api/auth/me").get_json()["user"]
    assert me["acting_org"] == alpha["id"], me
    owner.post("/api/auth/logout")
    again = login("admin@example.com", "admin123")
    assert again.get("/api/session/org").get_json()["acting_org"] is None
    assert again.get("/api/auth/me").get_json()["user"]["acting_org"] is None
    print("re-login clears the acting client")


def test_no_client_means_no_new_work():
    """With nobody picked, work is refused instead of saved where no one can find it."""
    owner = login("admin@example.com", "admin123")
    owner.post("/api/session/org", json={"org_id": None})
    resp = owner.post("/api/jobs", json={"prompt": "write an email"})
    assert resp.status_code == 400, resp.get_data(as_text=True)
    assert "workspace switcher" in resp.get_json()["error"].lower(), resp.get_json()
    print("POST /api/jobs with no client -> 400:", resp.get_json()["error"])

    # The "+ New draft" button goes through this one, and it used to write unowned rows.
    draft = owner.post("/api/deliverables", json={"kind": "email", "title": "Nobody's"})
    assert draft.status_code == 400, draft.get_data(as_text=True)
    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT id FROM deliverables WHERE title = ?", ("Nobody's",)
        ).fetchall()
    assert not rows, "an unowned draft row was created: %s" % rows
    print("POST /api/deliverables with no client -> 400, no row written")


def test_save_deliverable_refuses_an_unowned_job():
    job_id = jobs.create_job("orphan check", "write an email", None, "selector", "agent_team")
    tool = tool_registry.resolve(["save_deliverable"], job_id=job_id)[0]
    reply = asyncio.run(tool.run_json(
        {"kind": "email", "title": "Nobody's email",
         "content": "<html><body><p>hi</p></body></html>"},
        DRY,
    ))
    assert "no client" in reply.lower(), reply
    with db.get_connection() as conn:
        rows = conn.execute("SELECT id FROM deliverables WHERE job_id = ?", (job_id,)).fetchall()
    assert not rows, "an unowned draft was written anyway: %s" % rows
    with db.get_connection() as conn:
        conn.execute("DELETE FROM job_events WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    print("refusal:", reply)
    print("no row written for the unowned job")


def test_a_site_created_while_acting_as_a_client_belongs_to_it(alpha, beta):
    """The switcher is the tenant for a site, not only for a job or a draft.

    Without the fallback the row was written with org_id NULL: invisible to the client,
    and skipped by the DNS sweep, so a correctly pointed domain never turned green.
    """
    owner = login("admin@example.com", "admin123")
    owner.post("/api/session/org", json={"org_id": alpha["id"]})
    domain = "site-%s.example.test" % TOKEN
    created = owner.post("/api/sites", json={"domain": domain, "target": "local"})
    assert created.status_code == 201, created.get_data(as_text=True)
    site = created.get_json()["site"]
    assert site["org_id"] == alpha["id"], site
    print("acting as A: the site row belongs to org %s" % alpha["id"])

    a_ids = [row["id"] for row in
             login(ALPHA["email"], ALPHA["password"]).get("/api/sites").get_json()["sites"]]
    assert site["id"] in a_ids, a_ids
    b_ids = [row["id"] for row in
             login(BETA["email"], BETA["password"]).get("/api/sites").get_json()["sites"]]
    assert site["id"] not in b_ids, b_ids
    print("client A sees it, client B does not")
    sites_service.delete_site(site["id"])


def test_the_dns_sweep_checks_an_unowned_site():
    """An org_id NULL site is the one nobody can refresh from a client workspace.

    Skipping it in the sweep is how a domain that points here stays "pending dns" forever.
    """
    site = sites_service.create_site(
        {"domain": "sweep-%s.example.test" % TOKEN, "target": "local"}, org_id=None)
    assert site["org_id"] is None

    original_list = sites_service.list_sites
    original_report = provisioner.dns_report
    original_probe = provisioner.live_probe
    sites_service.list_sites = lambda limit=200: [site]
    provisioner.dns_report = lambda domain, server_ip_value=None: {
        "ok": True, "resolved": ["203.0.113.9"], "reason": "", "proxied": False}
    provisioner.live_probe = lambda domain, timeout=6, fetcher=None: {
        "ok": False, "error": "", "hint": "certificate pending"}
    try:
        checked = scheduler.verify_pending()
    finally:
        sites_service.list_sites = original_list
        provisioner.dns_report = original_report
        provisioner.live_probe = original_probe

    fresh = sites_service.get_site(site["id"])
    assert checked == 1, checked
    assert fresh["status"] == "dns_ok", fresh
    print("the sweep refreshed an unowned site to", fresh["status"])
    sites_service.delete_site(site["id"])


def main():
    alpha, beta = make_clients()
    try:
        test_owner_picks_a_client_and_the_lists_follow(alpha, beta)
        test_only_a_real_client_id_is_accepted()
        test_a_client_cannot_switch_clients(alpha, beta)
        test_a_fresh_login_starts_at_all_clients(alpha)
        test_no_client_means_no_new_work()
        test_save_deliverable_refuses_an_unowned_job()
        test_a_site_created_while_acting_as_a_client_belongs_to_it(alpha, beta)
        test_the_dns_sweep_checks_an_unowned_site()
    finally:
        cleanup()
    print("\nWORKSPACE OK - the switcher scopes every list, and unowned work is refused")


if __name__ == "__main__":
    main()