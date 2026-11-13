"""Removing a domain must disconnect it, not merely forget the row.

Run: .\\venv\\Scripts\\python.exe tests\\test_domain_removal.py

The old DELETE route removed only the ``sites`` row. Caddy still had the site block, so
the domain kept serving after the console said it was gone; deleting a client made the
row invisible but left the same vhost running. These checks pin the replacement: one
operator decision, a preapproved vhost removal and reload, the row deleted only after
the write succeeds, and files/history kept. They also pin the owner-wide list that makes
an already-orphaned domain findable again.
"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="domain-removal-"), "test.db").replace("\\", "/")
os.environ.pop("ALLOW_LIVE_ACTIONS", None)
os.environ.pop("JOB_BACKEND", None)
os.environ.pop("REDIS_URL", None)

import actions  # noqa: E402
import app as appmod  # noqa: E402
import db  # noqa: E402
import deliverables  # noqa: E402
import jobs  # noqa: E402
import orgs as orgs_service  # noqa: E402
import provisioner  # noqa: E402
import sites as sites_service  # noqa: E402

FAILED = []


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


class live_actions:
    """Make actions.gate the real live branch, but keep approval waits out of tests."""

    def __enter__(self):
        self.original = (actions.is_dry_run, actions.approval_timeout)
        actions.is_dry_run = lambda: False
        actions.approval_timeout = lambda: 1
        return self

    def __exit__(self, *exc):
        actions.is_dry_run, actions.approval_timeout = self.original


def new_org(label, email=None):
    data = {"name": label, "slug": label.lower().replace(" ", "-")}
    if email:
        data.update({"email": email, "password": "remove-pass-1234",
                     "contact_name": label})
    return orgs_service.create_org(data)


def new_site(domain, org_id, status="live"):
    docroot = tempfile.mkdtemp(prefix="domain-root-")
    created = sites_service.create_site({"domain": domain, "docroot": docroot},
                                        org_id=org_id)
    sites_service.update_site(created["id"], {"status": status})
    return sites_service.get_site(created["id"])


def login(email, password="admin123"):
    client = appmod.app.test_client()
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return client


def fake_removal(calls, result=None):
    """A standing in for the vhost removal, recording how it was authorised."""
    def remove(site, **kwargs):
        calls.append({"site": site, "kwargs": kwargs})
        return result or {"ok": True, "dry_run": False,
                          "steps": [{"step": "remove_vhost", "ok": True,
                                     "detail": "removed"},
                                    {"step": "reload", "ok": True,
                                     "detail": "reloaded"}]}
    return remove


def site_delete_rows(domain):
    return [row for row in actions.list_approvals(limit=200)
            if row.get("tool") == "site_delete" and domain in (row.get("summary") or "")]


def test_live_removal_keeps_files_and_history():
    org = new_org("Removal Live")
    site = new_site("remove-live.test", org["id"])
    os.makedirs(site["docroot"], exist_ok=True)
    index = os.path.join(site["docroot"], "index.html")
    with open(index, "w", encoding="utf-8") as handle:
        handle.write("<h1>keep me</h1>")
    item = deliverables.create_deliverable(
        {"kind": "website", "title": "Home", "slug": "home", "status": "published",
         "content": "<h1>keep me</h1>", "site_id": site["id"],
         "url": "https://remove-live.test/"},
        org_id=org["id"])
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO site_versions (site_id, org_id, content_html, source, path) "
            "VALUES (?, ?, ?, ?, ?)",
            (site["id"], org["id"], "<h1>old</h1>", "test", "index.html"))

    calls = []
    original = provisioner.remove_site
    provisioner.remove_site = fake_removal(calls)
    try:
        with live_actions():
            result = sites_service.disconnect_site(site["id"], actor="operator@test")
    finally:
        provisioner.remove_site = original

    check("the removal succeeds", result.get("ok"), True)
    check("the row is gone", sites_service.get_site(site["id"]), None)
    check("the vhost removal was covered by the click", calls[0]["kwargs"].get("preapproved"), True)
    check("the page is detached, not deleted", (deliverables.get_deliverable(item["id"]) or {}).get("site_id"), None)
    stored = deliverables.get_deliverable(item["id"]) or {}
    check("and returns to draft", stored.get("status"), "draft")
    check("with the stale live URL cleared", stored.get("url"), "")
    with db.get_connection() as conn:
        versions = conn.execute(
            "SELECT COUNT(*) FROM site_versions WHERE site_id = ?", (site["id"],)).fetchone()[0]
    check("the version history survives", versions, 1)
    check("the HTML file survives", os.path.exists(index), True)
    rows = site_delete_rows("remove-live.test")
    check("exactly one approval is recorded", len(rows), 1)
    check("approved by the clicker", rows[0]["decided_by"], "operator@test")
    check("and nothing is left pending", rows[0]["status"], "approved")
    check("the response says what was kept", "files" in result.get("message", ""), True)


def test_a_dry_run_removes_nothing():
    org = new_org("Removal Dry")
    site = new_site("remove-dry.test", org["id"])
    original = actions.is_dry_run
    actions.is_dry_run = lambda: True
    try:
        result = sites_service.disconnect_site(site["id"], actor="dry@test")
    finally:
        actions.is_dry_run = original

    check("the dry-run reports success", result.get("ok"), True)
    check("but says it was simulated", result.get("dry_run"), True)
    check("and did not claim to remove", result.get("removed"), False)
    check("the site row remains", (sites_service.get_site(site["id"]) or {}).get("id"), site["id"])
    check("no approval was recorded", site_delete_rows("remove-dry.test"), [])
    check("the message explains the simulation", "Dry-run" in result.get("message", ""), True)


def test_a_failed_vhost_removal_can_be_retried():
    org = new_org("Removal Retry")
    site = new_site("remove-retry.test", org["id"])
    calls = []
    responses = [
        {"ok": False, "steps": [{"step": "remove_vhost", "ok": False,
                                "detail": "permission denied"}]},
        {"ok": True, "dry_run": False,
         "steps": [{"step": "remove_vhost", "ok": True, "detail": "removed"},
                   {"step": "reload", "ok": True, "detail": "reloaded"}]},
    ]

    def flaky(site_arg, **kwargs):
        calls.append(kwargs)
        return responses[len(calls) - 1]

    original = provisioner.remove_site
    provisioner.remove_site = flaky
    try:
        with live_actions():
            first = sites_service.disconnect_site(site["id"], actor="retry@test")
            after_first = sites_service.get_site(site["id"])
            second = sites_service.disconnect_site(site["id"], actor="retry@test")
    finally:
        provisioner.remove_site = original

    check("the first attempt fails", first.get("ok"), False)
    check("the row is kept for the retry", (after_first or {}).get("id"), site["id"])
    check("the failure names the step", "remove_vhost" in first.get("error", ""), True)
    check("the retry succeeds", second.get("ok"), True)
    check("and removes the row", sites_service.get_site(site["id"]), None)
    check("both attempts were preapproved", [c.get("preapproved") for c in calls], [True, True])


def test_a_client_can_remove_their_own_domain():
    org = new_org("Removal Client", "client@remove.test")
    site = new_site("remove-client.test", org["id"])
    calls = []
    original = provisioner.remove_site
    provisioner.remove_site = fake_removal(calls)
    try:
        with live_actions():
            response = login("client@remove.test", "remove-pass-1234").delete(
                "/api/sites/%d" % site["id"])
    finally:
        provisioner.remove_site = original
    check("a client's own DELETE succeeds", response.status_code, 200)
    check("the client's domain is gone", sites_service.get_site(site["id"]), None)
    rows = site_delete_rows("remove-client.test")
    check("the client's click is the audit trail", len(rows), 1)
    check("and is attributed to them", rows[0]["decided_by"], "client@remove.test")


def test_an_active_provision_job_blocks_removal():
    org = new_org("Removal Busy")
    site = new_site("remove-busy.test", org["id"])
    job_id = jobs.create_job("Provision remove-busy.test", "remove-busy.test", None,
                             "selector", "site_provision", org_id=org["id"])
    check("the fixture job is open", (jobs.get_job(job_id) or {}).get("status"), "queued")
    result = sites_service.disconnect_site(site["id"], actor="busy@test")
    check("removal is refused with a retryable conflict", (result.get("ok"), result.get("busy")),
          (False, True))
    check("the message names the active run", "provisioning run" in result.get("error", ""), True)
    check("the site row is still there", (sites_service.get_site(site["id"]) or {}).get("id"), site["id"])
    check("and no removal approval was recorded", site_delete_rows("remove-busy.test"), [])


def test_another_clients_delete_is_a_404():
    alpha = new_org("Removal Alpha", "alpha@remove.test")
    beta = new_org("Removal Beta", "beta@remove.test")
    site = new_site("remove-beta.test", beta["id"])
    client = login("alpha@remove.test", "remove-pass-1234")
    response = client.delete("/api/sites/%d" % site["id"])
    check("the cross-tenant DELETE is not found", response.status_code, 404)
    check("and the target row is untouched", (sites_service.get_site(site["id"]) or {}).get("id"),
          site["id"])
    check("nothing was approved", site_delete_rows("remove-beta.test"), [])


def test_the_owner_can_remove_an_orphan():
    orphan = new_site("remove-orphan.test", 987654)
    calls = []
    original = provisioner.remove_site
    provisioner.remove_site = fake_removal(calls)
    try:
        with live_actions():
            response = login("admin@example.com").delete("/api/sites/%d" % orphan["id"])
    finally:
        provisioner.remove_site = original
    check("the owner's request succeeds", response.status_code, 200)
    body = response.get_json() or {}
    check("the orphan row is gone", sites_service.get_site(orphan["id"]), None)
    check("the response records the removal", body.get("removed"), True)
    rows = site_delete_rows("remove-orphan.test")
    check("the owner's click is the audit trail", len(rows), 1)
    check("and the approval says it was a deleted-client cleanup",
          '"legacy_cleanup": true' in (rows[0].get("args_json") or ""), True)


def test_the_platform_list_is_owner_only_and_finds_orphans():
    org = new_org("Removal Platform", "platform@remove.test")
    owned = new_site("platform-owned.test", org["id"])
    orphan = new_site("platform-orphan.test", 987655)

    anonymous = appmod.app.test_client().get("/api/platform/domains")
    check("the owner-wide list needs a login", anonymous.status_code, 401)
    client = login("platform@remove.test", "remove-pass-1234")
    check("a client admin cannot see every domain", client.get("/api/platform/domains").status_code, 403)

    owner = login("admin@example.com")
    response = owner.get("/api/platform/domains")
    check("the owner can list every domain", response.status_code, 200)
    body = response.get_json() or {}
    domains = {row["domain"]: row for row in body.get("domains") or []}
    check("the owned site is present", "platform-owned.test" in domains, True)
    check("with its client name", domains["platform-owned.test"]["org_name"], org["name"])
    check("and not marked orphaned", domains["platform-owned.test"]["orphaned"], False)
    check("the orphan is present", "platform-orphan.test" in domains, True)
    check("with the deleted-client label", domains["platform-orphan.test"]["org_name"],
          "Deleted client")
    check("and is labelled orphaned", domains["platform-orphan.test"]["orphaned"], True)
    check("with no matching org", domains["platform-orphan.test"]["org_exists"], False)
    check("counts call out the orphan", body.get("counts", {}).get("orphaned") >= 1, True)
    check("the response reports the dry-run state", isinstance(body.get("dry_run"), bool), True)
    check("and includes the server address field", "server_ip" in body, True)


def test_a_client_with_a_domain_cannot_be_deleted():
    org = new_org("Removal Org Guard")
    site = new_site("org-guard.test", org["id"])
    try:
        orgs_service.delete_org(org["id"])
        refused = ""
    except orgs_service.OrgError as exc:
        refused = str(exc)
    check("deleting a client with a domain is refused", "connected domain" in refused, True)

    calls = []
    original = provisioner.remove_site
    provisioner.remove_site = fake_removal(calls)
    try:
        with live_actions():
            result = sites_service.disconnect_site(site["id"], actor="guard@test")
    finally:
        provisioner.remove_site = original
    check("the domain disconnects", result.get("ok"), True)
    check("then the empty client deletes", orgs_service.delete_org(org["id"]), True)


if __name__ == "__main__":
    test_live_removal_keeps_files_and_history()
    test_a_dry_run_removes_nothing()
    test_a_failed_vhost_removal_can_be_retried()
    test_an_active_provision_job_blocks_removal()
    test_a_client_can_remove_their_own_domain()
    test_another_clients_delete_is_a_404()
    test_the_owner_can_remove_an_orphan()
    test_the_platform_list_is_owner_only_and_finds_orphans()
    test_a_client_with_a_domain_cannot_be_deleted()
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nDOMAIN REMOVAL OK - the vhost goes, the files stay, orphans are reachable")
