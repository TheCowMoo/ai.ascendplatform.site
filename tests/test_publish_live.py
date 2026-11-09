"""Publishing a page must not wait for an approval the click already gave.

Run: .\\venv\\Scripts\\python.exe tests\\test_publish_live.py

The live publish path used to record an approval and then call actions.gate(), which
inserts a *second* pending row and blocks for APPROVAL_TIMEOUT (300s). This handler is
synchronous Flask on the uvicorn event loop, so two clicks held both workers: every other
request - including /api/sites - answered 502 until the waits expired. Approval timeout is
pinned to 1s here, so a regression fails in a second instead of hanging the suite.
"""

import io
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="publish-live-"), "test.db").replace("\\", "/")
os.environ.pop("ALLOW_LIVE_ACTIONS", None)
os.environ.pop("JOB_BACKEND", None)
os.environ.pop("REDIS_URL", None)

import actions  # noqa: E402
import app as appmod  # noqa: E402
import db  # noqa: E402
import deliverables  # noqa: E402
import provisioner  # noqa: E402
import sites as sites_service  # noqa: E402

db.init_db()

FAILED = []
ORG = 701
DOCROOT = tempfile.mkdtemp(prefix="publish-root-")


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


class live_actions:
    def __enter__(self):
        self.original = (actions.is_dry_run, actions.approval_timeout)
        actions.is_dry_run = lambda: False
        actions.approval_timeout = lambda: 1

    def __exit__(self, *exc):
        actions.is_dry_run, actions.approval_timeout = self.original


def page(slug, title, content):
    return deliverables.create_deliverable(
        {"kind": "website", "title": title, "slug": slug, "content": content},
        org_id=ORG,
    )


def site(domain, status):
    created = sites_service.create_site(
        {"domain": domain, "docroot": os.path.join(DOCROOT, domain, "public_html")},
        org_id=ORG,
    )
    sites_service.update_site(created["id"], {"status": status})
    return sites_service.get_site(created["id"])


def test_a_live_publish_does_not_wait():
    target_site = site("live-publish.test", "live")
    item = page("home", "Home", "<h1>live</h1>")
    started = time.time()
    with live_actions():
        result = deliverables.publish_deliverable(item["id"], site_id=target_site["id"],
                                                 actor="tester")
    elapsed = time.time() - started
    check("the publish returns at once", elapsed < 5, True)
    check("it succeeded", result["ok"], True)
    check("not a dry-run", result["dry_run"], False)
    check("on the homepage", result["url"], "https://live-publish.test/")
    check("with no warning for a live site", result["warning"], "")
    written = os.path.join(DOCROOT, "live-publish.test", "public_html", "index.html")
    with open(written, encoding="utf-8") as handle:
        check("the file is on disk", handle.read(), "<h1>live</h1>")
    stored = deliverables.get_deliverable(item["id"])
    check("the page is published", stored["status"], "published")
    check("and points at the site", stored["site_id"], target_site["id"])
    rows = actions.list_approvals(limit=50)
    check("exactly one audit row", len(rows), 1)
    check("approved by the operator", rows[0]["decided_by"], "tester")
    check("and nothing is left waiting", actions.list_approvals(status="pending"), [])
    check("the row carries the write result", "wrote" in (rows[0].get("result") or ""), True)


def test_a_site_that_is_not_live_says_so():
    target_site = site("not-live.test", "dns_ok")
    item = page("about", "About", "<h1>about</h1>")
    with live_actions():
        result = deliverables.publish_deliverable(item["id"], site_id=target_site["id"],
                                                 actor="tester")
    check("the write still happens", result["ok"], True)
    check("but the response warns", "not live yet" in result["warning"], True)
    check("and names where it will appear", "not-live.test/about.html" in result["warning"], True)


def test_the_route_answers_instead_of_blocking():
    target_site = site("route-publish.test", "live")
    item = page("contact", "Contact", "<h1>contact</h1>")
    client = appmod.app.test_client()
    check("the route needs a login",
          appmod.app.test_client().post(
              "/api/deliverables/%d/publish" % item["id"],
              json={"site_id": target_site["id"]}).status_code, 401)
    client.post("/api/auth/login", json={"email": "admin@example.com",
                                        "password": "admin123"})
    started = time.time()
    with live_actions():
        resp = client.post("/api/deliverables/%d/publish" % item["id"],
                           json={"site_id": target_site["id"]})
    elapsed = time.time() - started
    check("the request answers at once", elapsed < 5, True)
    check("with a success", resp.status_code, 200)
    check("and a published url", (resp.get_json() or {}).get("url"),
          "https://route-publish.test/contact.html")


def test_the_console_posts_once():
    """The multi-site modal used to POST here and again inside sendPublish: two writes and
    two approvals for one click. One call site is the contract."""
    source = io.open("admin/admin.js", encoding="utf-8").read()
    assert source.count("'/publish'") == 1, source.count("'/publish'")
    print("ok   the console has one publish call site")


def test_dry_run_is_unchanged():
    target_site = site("dry-publish.test", "live")
    item = page("dry", "Dry", "<h1>dry</h1>")
    result = deliverables.publish_deliverable(item["id"], site_id=target_site["id"],
                                              actor="tester")
    check("dry-run stays a simulation", result["dry_run"], True)
    check("and leaves the page a draft",
          deliverables.get_deliverable(item["id"])["status"], "draft")
    check("without writing the file",
          os.path.exists(os.path.join(DOCROOT, "dry-publish.test", "public_html", "dry.html")),
          False)


if __name__ == "__main__":
    original_root = provisioner.SITES_ROOT
    provisioner.SITES_ROOT = DOCROOT
    try:
        test_a_live_publish_does_not_wait()
        test_a_site_that_is_not_live_says_so()
        test_the_route_answers_instead_of_blocking()
        test_the_console_posts_once()
        test_dry_run_is_unchanged()
    finally:
        provisioner.SITES_ROOT = original_root
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nPUBLISH LIVE OK - one approval, no wait, no worker starvation")
