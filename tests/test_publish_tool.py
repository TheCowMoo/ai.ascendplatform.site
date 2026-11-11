"""Publishing from the team and from the voice.

Run: .\\venv\\Scripts\\python.exe tests\\test_publish_tool.py

The tool that the Manager, automations and the voice share. It must pick the client's
page and site without guessing, refuse another client's draft, and record the person who
asked as the approval's decision-maker.
"""

import asyncio
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="publish-tool-"), "test.db").replace("\\", "/")
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
import tools.site as site_tools  # noqa: E402

db.init_db()

FAILED = []
ORG = 801
OTHER = 802
VOICE_ORG = 803
DOCROOT = tempfile.mkdtemp(prefix="publish-tool-root-")


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


def user_id():
    with db.get_connection() as conn:
        row = conn.execute("SELECT id FROM users WHERE email = ?",
                           ("admin@example.com",)).fetchone()
    return row["id"]


def make_site(domain, org=ORG):
    return sites_service.create_site(
        {"domain": domain, "docroot": os.path.join(DOCROOT, domain, "public_html")},
        org_id=org)


def make_page(title, org=ORG, slug="home"):
    return deliverables.create_deliverable(
        {"kind": "website", "title": title, "slug": slug,
         "content": "<h1>%s</h1>" % title}, org_id=org)


def make_job(org=ORG, deliverable_id=None):
    return jobs.create_job("Publish", "publish it", user_id(), "selector", "chat",
                           org_id=org, deliverable_id=deliverable_id)


def publish_with_tool(job_id, **kwargs):
    fn = site_tools._build_publish_page(None, job_id=job_id)
    return asyncio.run(fn(**kwargs))


def test_the_tool_refuses_guesses():
    first = make_site("one.test")
    second = make_site("two.test")
    item = make_page("Home")
    job = make_job(deliverable_id=item["id"])
    check("two sites asks which one",
          "Ask which domain" in publish_with_tool(job), True)
    check("a named site is used",
          "[dry-run] would publish" in publish_with_tool(job, site_id=second["id"])
          and "two.test" in publish_with_tool(job, site_id=second["id"]), True)

    stranger = make_page("Theirs", org=OTHER)
    check("another client's draft is refused",
          "no draft" in publish_with_tool(job, deliverable_id=stranger["id"]), True)
    stranger_site = make_site("theirs.test", org=OTHER)
    check("another client's site is refused",
          "no site" in publish_with_tool(job, site_id=stranger_site["id"]), True)


def test_the_tool_publishes_and_signs_the_approval():
    target = make_site("tool-live.test")
    item = make_page("Landing", slug="home")
    job = make_job(deliverable_id=item["id"])
    with live_actions():
        message = publish_with_tool(job, site_id=target["id"])
    check("it says what it published", "Published" in message, True)
    check("and where", "https://tool-live.test/" in message, True)
    path = os.path.join(DOCROOT, "tool-live.test", "public_html", "index.html")
    check("the file is on disk", os.path.exists(path), True)
    rows = [row for row in actions.list_approvals(limit=50)
            if "publish" in (row.get("summary") or "").lower()]
    check("one audit row for this publish", len(rows), 1)
    check("with the requester's name", rows[0]["decided_by"], "admin@example.com")
    check("and nothing left pending", actions.list_approvals(status="pending"), [])


def test_the_voice_can_publish_after_confirming():
    client = appmod.app.test_client()
    client.post("/api/auth/login", json={"email": "admin@example.com",
                                        "password": "admin123"})
    org = orgs_service.create_org({"name": "Voice Client"})["id"]
    client.post("/api/session/org", json={"org_id": org})
    target = make_site("voice.test", org=org)
    item = make_page("Voice page", org=org)
    body = {"name": "publish_page", "turn_id": "voice-1",
            "artifact": {"kind": "deliverable", "id": item["id"]},
            "args": {"site_domain": target["domain"]}}
    with live_actions():
        resp = client.post("/api/voice/tool", json=body)
    data = resp.get_json() or {}
    check("the voice publishes", resp.status_code, 200)
    check("and reports the URL", data.get("url"), "https://voice.test/")
    check("in words", "Published" in (data.get("message") or ""), True)
    check("a retry does not publish twice",
          (client.post("/api/voice/tool", json=body).get_json() or {}).get("duplicate"), True)

    check("no canvas is refused",
          client.post("/api/voice/tool",
                      json={"name": "publish_page", "args": {}}).status_code, 400)
    check("an unknown tool is refused",
          client.post("/api/voice/tool",
                      json={"name": "rm_rf", "args": {}}).status_code, 403)


def test_voice_asks_when_there_are_several_sites():
    client = appmod.app.test_client()
    client.post("/api/auth/login", json={"email": "admin@example.com",
                                        "password": "admin123"})
    org = orgs_service.create_org({"name": "Many Sites"})["id"]
    client.post("/api/session/org", json={"org_id": org})
    make_site("voice-a.test", org=org)
    make_site("voice-b.test", org=org)
    item = make_page("Many", org=org)
    resp = client.post("/api/voice/tool", json={
        "name": "publish_page", "turn_id": "voice-2",
        "artifact": {"kind": "deliverable", "id": item["id"]}, "args": {}})
    data = resp.get_json() or {}
    check("several sites is a 400", resp.status_code, 400)
    check("naming the domains", sorted(data.get("domains") or []),
          ["voice-a.test", "voice-b.test"])
    check("nothing was written for it",
          deliverables.get_deliverable(item["id"])["status"], "draft")


if __name__ == "__main__":
    original_root = provisioner.SITES_ROOT
    provisioner.SITES_ROOT = DOCROOT
    try:
        test_the_tool_refuses_guesses()
        test_the_tool_publishes_and_signs_the_approval()
        test_the_voice_can_publish_after_confirming()
        test_voice_asks_when_there_are_several_sites()
    finally:
        provisioner.SITES_ROOT = original_root
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nPUBLISH TOOL OK - page and site resolved, approval signed, voice publishes once")
