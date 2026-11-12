"""Home-page publishing, live-page replacement, and the per-file undo.

Run: .\\venv\\Scripts\\python.exe tests\\test_publish_home.py

The bug this pins: a page called "Cow Barn Tour - Home" got the slug
cow-barn-tour-home, so Publish wrote /cow-barn-tour-home.html and the site root kept
the starter page. A second bug: a publish over an existing different page replaced it
with no confirmation and the site version recorded the home page, not the file.
"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DOCROOT = tempfile.mkdtemp(prefix="publish-home-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="publish-home-db-"), "test.db").replace("\\", "/")
os.environ.pop("ALLOW_LIVE_ACTIONS", None)
os.environ.pop("JOB_BACKEND", None)
os.environ.pop("REDIS_URL", None)

import actions  # noqa: E402
import db  # noqa: E402
import deliverables  # noqa: E402
import provisioner  # noqa: E402
import sites as sites_service  # noqa: E402
import versions  # noqa: E402

db.init_db()
provisioner.SITES_ROOT = DOCROOT
FAILED = []
ORG = 901
OTHER = 902


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


def make_site(domain, org=ORG):
    created = sites_service.create_site(
        {"domain": domain, "docroot": os.path.join(DOCROOT, domain, "public_html")},
        org_id=org)
    sites_service.update_site(created["id"], {
        "status": "live",
        "content_html": "<html><body><p>This site is ready. Add content in the Sites tab.</p></body></html>",
    })
    return sites_service.get_site(created["id"])


def page(title, content, org=ORG, slug="cow-barn-tour-home"):
    return deliverables.create_deliverable(
        {"kind": "website", "title": title, "slug": slug, "content": content}, org_id=org)


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def test_as_home_writes_index_and_records_the_choice():
    target = make_site("home-publish.test")
    item = page("Cow Barn Tour - Home", "<h1>Cow Barn Tour</h1>")
    with live_actions():
        result = deliverables.publish_deliverable(
            item["id"], site_id=target["id"], actor="tester", as_home=True)
    index = os.path.join(DOCROOT, "home-publish.test", "public_html", "index.html")
    check("it wrote index.html", read(index), "<h1>Cow Barn Tour</h1>")
    check("the URL is the root", result["url"], "https://home-publish.test/")
    check("it says home", result["as_home"], True)
    stored = deliverables.get_deliverable(item["id"])
    check("the draft is published", stored["status"], "published")
    check("and its slug is now index", stored["slug"], "index")
    check("the site row mirrors the home page",
          "Cow Barn Tour" in sites_service.get_site(target["id"])["content_html"], True)
    version_rows = versions.versions(target["id"])
    check("the starter home page has a version with its path",
          any(row["path"] == "index.html" for row in version_rows), True)


def test_a_subpage_publish_records_that_file_not_the_home_page():
    target = make_site("sub-publish.test")
    item = page("About", "<h1>About</h1>", slug="about")
    with live_actions():
        result = deliverables.publish_deliverable(
            item["id"], site_id=target["id"], actor="tester")
    check("it writes about.html", os.path.exists(
        os.path.join(DOCROOT, "sub-publish.test", "public_html", "about.html")), True)
    check("the URL is the subpage", result["url"], "https://sub-publish.test/about.html")
    check("it warns that the root is still the starter",
          "starter page" in result["warning"], True)
    check("the site row keeps the starter home page",
          "This site is ready" in sites_service.get_site(target["id"])["content_html"], True)
    version_rows = versions.versions(target["id"])
    check("the version names about.html",
          any(row["path"] == "about.html" for row in version_rows), True)


def test_a_live_page_is_replaced_only_with_a_confirmation():
    target = make_site("replace.test")
    item = page("Report", "<h1>QA report</h1>", slug="report")
    path = os.path.join(DOCROOT, "replace.test", "public_html", "report.html")
    with live_actions():
        deliverables.publish_deliverable(item["id"], site_id=target["id"], actor="tester")
        # The same draft, edited after it went live: this is the QA-report case. A
        # different draft with the same slug is refused earlier, by the slug conflict.
        deliverables.update_deliverable(item["id"], {"content": "<h1>Real page</h1>"})
        try:
            deliverables.publish_deliverable(item["id"], site_id=target["id"],
                                             actor="tester")
        except deliverables.DeliverableError as exc:
            refused = "already has content" in str(exc)
        else:
            refused = False
        check("the edited page is refused without confirmation", refused, True)
        check("the live page is unchanged", read(path), "<h1>QA report</h1>")
        replaced = deliverables.publish_deliverable(
            item["id"], site_id=target["id"], actor="tester", replace=True)
    check("the confirmed replace succeeds", replaced["ok"], True)
    check("and the file changes", read(path), "<h1>Real page</h1>")


def test_restore_writes_the_file_the_snapshot_came_from():
    target = make_site("restore.test")
    item = page("About", "<h1>About v1</h1>", slug="about")
    path = os.path.join(DOCROOT, "restore.test", "public_html", "about.html")
    with live_actions():
        deliverables.publish_deliverable(item["id"], site_id=target["id"], actor="tester")
        # The next publish snapshots v1 before writing v2. That snapshot is the undo.
        deliverables.update_deliverable(item["id"], {"content": "<h1>About v2</h1>"})
        deliverables.publish_deliverable(item["id"], site_id=target["id"], actor="tester",
                                         replace=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("<h1>bad report</h1>")
        row = next(row for row in versions.versions(target["id"])
                   if row["path"] == "about.html"
                   and row["bytes"] == len("<h1>About v1</h1>"))
        versions.restore(row["id"], actor="tester")
    check("the file is back", read(path), "<h1>About v1</h1>")
    check("the restore is itself versioned",
          any(row["source"] == "before-restore" for row in versions.versions(target["id"])),
          True)


if __name__ == "__main__":
    try:
        test_as_home_writes_index_and_records_the_choice()
        test_a_subpage_publish_records_that_file_not_the_home_page()
        test_a_live_page_is_replaced_only_with_a_confirmation()
        test_restore_writes_the_file_the_snapshot_came_from()
    finally:
        if FAILED:
            print("\n%d FAILED" % len(FAILED))
            raise SystemExit(1)
        print("\nPUBLISH HOME OK - root/subpage is explicit, replacement is confirmed, undo writes the file")
