"""Platform client detail: six tabs and a client-scoped domain filter.

Run: .\\venv\\Scripts\\python.exe tests\\test_client_detail.py

The UI is still a no-build JavaScript app, so the tab shape is checked from the
source. The domain filter is exercised through the real Flask app: the operator can
narrow the list, a client cannot reach it, and omitting ``org_id`` keeps the old
owner-wide response.
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("NOTIFICATIONS_ENABLED", "0")

import app as appmod  # noqa: E402
import db  # noqa: E402
import orgs as orgs_service  # noqa: E402
import sites as sites_service  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TAG = uuid.uuid4().hex[:8]
ALPHA_NAME = "Client Detail Alpha %s" % TAG
BETA_NAME = "Client Detail Beta %s" % TAG
ALPHA_EMAIL = "client-detail-alpha-%s@example.test" % TAG
BETA_EMAIL = "client-detail-beta-%s@example.test" % TAG
ALPHA_DOMAIN = "client-detail-alpha-%s.test" % TAG
BETA_DOMAIN = "client-detail-beta-%s.test" % TAG
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
        conn.execute("DELETE FROM sites WHERE domain IN (?, ?)", (ALPHA_DOMAIN, BETA_DOMAIN))
        for name in (ALPHA_NAME, BETA_NAME):
            org = conn.execute("SELECT id FROM orgs WHERE name = ?", (name,)).fetchone()
            if org:
                conn.execute("DELETE FROM users WHERE org_id = ?", (org["id"],))
                conn.execute("DELETE FROM orgs WHERE id = ?", (org["id"],))


def setup():
    cleanup()
    db.init_db()
    alpha = orgs_service.create_org({
        "name": ALPHA_NAME, "email": ALPHA_EMAIL, "password": "client-detail-alpha-pass",
    })
    beta = orgs_service.create_org({
        "name": BETA_NAME, "email": BETA_EMAIL, "password": "client-detail-beta-pass",
    })
    assert alpha and beta, (alpha, beta)
    STATE["alpha"] = alpha
    STATE["beta"] = beta
    STATE["alpha_site"] = sites_service.create_site(
        {"domain": ALPHA_DOMAIN, "target": "local"}, org_id=alpha["id"])
    STATE["beta_site"] = sites_service.create_site(
        {"domain": BETA_DOMAIN, "target": "local"}, org_id=beta["id"])


def test_the_page_has_six_url_addressable_tabs():
    with open(os.path.join(ROOT, "admin", "admin.js"), encoding="utf-8") as handle:
        script = handle.read()
    tabs = ["overview", "team", "websites", "usage", "support", "settings"]
    for tab in tabs:
        assert "id: '%s'" % tab in script, "%s tab is missing" % tab
    assert "function viewClient(orgId, tab)" in script, "the client view is not tab-aware"
    assert "rest[1] || 'overview'" in script, "old client URLs do not default to Overview"
    for marker in ("Open workspace", "Report issue", "Email sending", "Danger zone",
                   "Workspace ID", "No support issues for this client"):
        assert marker in script, "%s is missing from the client page" % marker
    assert "function openBugModal" in script, "the shared bug editor is missing"
    assert "lockClient" in script, "the Support tab cannot lock the bug to its client"
    assert "peopleTable(people, true, [org], reload, org.id)" in script, \
        "the Team tab does not use the fixed-org table"
    assert "payload.org_id = fixedOrgId" in script, \
        "a fixed-org team edit can move a user to another client"
    with open(os.path.join(ROOT, "admin", "admin.css"), encoding="utf-8") as handle:
        css = handle.read()
    assert ".client-tabs" in css and ".client-stats" in css, \
        "the client detail layout has no styles"
    print("six tabs, shared bug editor and client styles are present")


def test_domains_can_be_filtered_to_one_client():
    client = owner()
    scoped = client.get("/api/platform/domains?org_id=%d" % STATE["alpha"]["id"])
    assert scoped.status_code == 200, scoped.get_data(as_text=True)
    body = scoped.get_json()
    assert body["org_id"] == STATE["alpha"]["id"], body
    assert body["domains"], body
    assert all(row["org_id"] == STATE["alpha"]["id"] for row in body["domains"]), body
    assert body["counts"]["total"] == len(body["domains"]), body
    assert STATE["beta_site"]["domain"] not in [row["domain"] for row in body["domains"]]

    unscoped = client.get("/api/platform/domains")
    assert unscoped.status_code == 200, unscoped.get_data(as_text=True)
    domains = [row["domain"] for row in unscoped.get_json()["domains"]]
    assert ALPHA_DOMAIN in domains and BETA_DOMAIN in domains, domains

    bad = client.get("/api/platform/domains?org_id=not-a-number")
    assert bad.status_code == 400, bad.get_data(as_text=True)
    print("owner can scope domains to one client; unfiltered response is unchanged")


def test_a_client_cannot_use_the_platform_domain_filter():
    alpha = login(ALPHA_EMAIL, "client-detail-alpha-pass")
    for path in ("/api/platform/domains?org_id=%d" % STATE["alpha"]["id"],
                 "/api/platform/domains"):
        response = alpha.get(path)
        assert response.status_code == 403, (path, response.status_code)
    print("a client admin is refused the owner-only domain filter")


def test_client_settings_use_the_same_org_scope():
    client = owner()
    response = client.get("/api/settings?org_id=%d" % STATE["alpha"]["id"])
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["scoped_to"] == STATE["alpha"]["id"], body
    assert [group["id"] for group in body["groups"]] == ["email", "analytics_automation"], body
    keys = {field["key"] for group in body["groups"] for field in group["fields"]}
    assert all(key.startswith("SMTP_") or key in (
        "ANALYTICS_AUTONOMY", "ANALYTICS_ALERTS_ENABLED") for key in keys), keys

    alpha = login(ALPHA_EMAIL, "client-detail-alpha-pass")
    response = alpha.get("/api/settings?org_id=%d" % STATE["beta"]["id"])
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["scoped_to"] == STATE["alpha"]["id"], response.get_json()
    print("client email settings stay on the selected/current org")


def main():
    try:
        setup()
        test_the_page_has_six_url_addressable_tabs()
        test_domains_can_be_filtered_to_one_client()
        test_a_client_cannot_use_the_platform_domain_filter()
        test_client_settings_use_the_same_org_scope()
    finally:
        cleanup()
    print("\nCLIENT DETAIL OK - six tabs, scoped domains, no client access")


if __name__ == "__main__":
    main()
