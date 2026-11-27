"""The hosting tools must never see or write another client's domain.

Run: .\\venv\\Scripts\\python.exe tests\\test_site_tool_scope.py

This is the cross-tenant hole found while adding the marketing tools: the team's
site tools called ``sites.list_sites()`` and ``upsert_for_agent()`` without the
job's org, so a team run for one client could list, provision or overwrite another
client's site. The Manager's own site tool was already scoped; these checks pin the
Operator/automation path too.
"""

import asyncio
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="site-tool-scope-"), "test.db").replace("\\", "/")
os.environ["SCHEDULER_ENABLED"] = "0"
os.environ.pop("ALLOW_LIVE_ACTIONS", None)
os.environ.pop("JOB_BACKEND", None)
os.environ.pop("REDIS_URL", None)

import app as appmod  # noqa: E402,F401
import db  # noqa: E402
import jobs  # noqa: E402
import orgs as orgs_service  # noqa: E402
import sites as sites_service  # noqa: E402
import tools.site as site_tools  # noqa: E402
import user_service  # noqa: E402

db.init_db()
FAILED = []


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name
          + ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


def admin_id():
    with db.get_connection() as conn:
        row = conn.execute("SELECT id FROM users WHERE email = ?",
                           ("admin@example.com",)).fetchone()
    return row["id"] if row else None


def new_org(name):
    return orgs_service.create_org({"name": name})["id"]


def new_job(org_id, requester=None):
    return jobs.create_job("Site scope", "test", requester or admin_id(),
                           "selector", "chat", org_id=org_id)


def run(builder, job_id, **kwargs):
    return asyncio.run(builder(None, job_id=job_id)(**kwargs))


def test_site_tools_are_scoped_to_the_job_org():
    org_a = new_org("Site Scope A")
    org_b = new_org("Site Scope B")
    site_a = sites_service.create_site({"domain": "scope-a.test"}, org_id=org_a)
    site_b = sites_service.create_site({"domain": "scope-b.test"}, org_id=org_b)
    job_a = new_job(org_a)

    listing = run(site_tools._build_list_sites, job_a)
    check("the list includes its own site", "scope-a.test" in listing, True)
    check("the list excludes the other client", "scope-b.test" in listing, False)
    check("a jobless list asks for a client",
          "Pick a client" in asyncio.run(site_tools._build_list_sites(None, None)()), True)

    refused = run(site_tools._build_write_site_content, job_a,
                  domain="scope-b.test", html="<p>overwrite</p>")
    check("write_site_content refuses another client's domain",
          "No site record" in refused, True)
    check("the other site was not written",
          (sites_service.get_site(site_b["id"]) or {}).get("content_html"), "")

    provisioned = run(site_tools._build_provision_site, job_a, domain="scope-b.test")
    check("provision_site refuses another client's domain",
          "another client" in provisioned, True)
    dns = run(site_tools._build_check_site_dns, job_a, domain="scope-b.test")
    check("check_site_dns refuses another client's domain",
          "No site record" in dns, True)

    job = {"id": 9001, "prompt": "scope-b.test", "requester_id": admin_id(),
           "org_id": org_a}
    task_result = asyncio.run(site_tools.site_provision_task(job, lambda *a: None))
    check("the provisioning job task refuses another client's domain",
          "another client" in task_result, True)

    try:
        sites_service.upsert_for_agent("scope-b.test", admin_id(), org_id=org_a)
        raised = False
    except sites_service.SiteError as exc:
        raised = "another client" in str(exc)
    check("upsert_for_agent refuses an existing foreign domain", raised, True)

    member = user_service.create_user({
        "email": "member-site@example.test", "name": "Member",
        "password": "member-pass-123", "role": "member", "org_id": org_a,
    }, admin_id(), True, None)
    denied = run(site_tools._build_provision_site, new_job(org_a, member["id"]),
                 domain="scope-a.test")
    check("a member cannot provision through the team tool",
          "Only a client admin" in denied, True)
    denied_write = run(site_tools._build_write_site_content,
                       new_job(org_a, member["id"]), domain="scope-a.test",
                       html="<p>x</p>")
    check("a member cannot write a team site page",
          "Only a client admin" in denied_write, True)


if __name__ == "__main__":
    test_site_tools_are_scoped_to_the_job_org()
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nSITE TOOL SCOPE OK - one client per job, no foreign writes")
