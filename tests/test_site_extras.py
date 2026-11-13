"""The extras around a site: a live form, a button on the page, an applied automation.

Run: .\\venv\\Scripts\\python.exe tests\\test_site_extras.py
"""

import asyncio
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="site-extras-"), "test.db").replace("\\", "/")
os.environ.pop("ALLOW_LIVE_ACTIONS", None)
os.environ.pop("JOB_BACKEND", None)
os.environ.pop("REDIS_URL", None)

import app as appmod  # noqa: E402
import automations  # noqa: E402
import db  # noqa: E402
import deliverables  # noqa: E402
import forms as forms_service  # noqa: E402
import jobs  # noqa: E402
import orgs as orgs_service  # noqa: E402
import sites as sites_service  # noqa: E402
import tools.manager as manager_tools  # noqa: E402

db.init_db()
FAILED = []


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


def user_id():
    with db.get_connection() as conn:
        row = conn.execute("SELECT id FROM users WHERE email = ?",
                           ("admin@example.com",)).fetchone()
    return row["id"]


def new_org(name):
    return orgs_service.create_org({"name": name})["id"]


def new_job(org):
    return jobs.create_job("Extras", "build the extras", user_id(), "selector", "chat",
                           org_id=org)


def run(builder, **kwargs):
    return asyncio.run(builder(None, job_id=JOB)(**kwargs))


def test_a_form_is_published_and_gets_a_button():
    global JOB
    reply = run(manager_tools._build_create_form, name="Contact us", kind="form",
                questions=[{"label": "Email", "type": "email", "required": True}])
    check("the form is created live", "live" in reply, True)
    check("with its link", "/f/" in reply, True)
    rows = forms_service.list_forms(ORG)
    check("exactly one form was created", len(rows), 1)
    check("a published form exists", rows[0]["status"], "published")
    form = rows[0]

    # Once the client has a live domain, every link the Manager hands back belongs to
    # that domain - not to the platform the console happens to be served from.
    site = sites_service.create_site({"domain": "extras.test"}, org_id=ORG)
    sites_service.update_site(site["id"], {"status": "live"})
    reply = run(manager_tools._build_create_form, name="Callback",
                questions=[{"label": "Phone", "type": "phone"}])
    check("the Manager uses the client's live domain", "https://extras.test/f/" in reply, True)

    page = deliverables.create_deliverable(
        {"kind": "website", "title": "Home", "slug": "home",
         "content": "<html><body><main><h1>Hi</h1></main></body></html>"},
        org_id=ORG)
    reply = run(manager_tools._build_add_form_link, deliverable_id=page["id"],
                form_id=form["id"], label="Get a quote")
    check("the button is added", "Get a quote" in reply and "/f/" in reply, True)
    saved = deliverables.get_deliverable(page["id"])["content"]
    check("the link is in the page", "/f/" + form["slug"] in saved, True)
    check("the button sits inside main", saved.index("/f/" + form["slug"]) < saved.index("</main>"), True)
    check("and nothing else was lost", "<h1>Hi</h1>" in saved, True)
    again = run(manager_tools._build_add_form_link, deliverable_id=page["id"],
                form_id=form["id"])
    check("a second call does not duplicate it", "already links" in again, True)


def test_another_clients_form_is_refused():
    other = new_org("Other Extras")
    page = deliverables.create_deliverable(
        {"kind": "website", "title": "Mine", "slug": "mine", "content": "<body></body>"},
        org_id=ORG)
    theirs = forms_service.create_form(
        {"name": "Theirs", "kind": "form",
         "questions": [{"id": "q1", "label": "Email", "type": "email",
                        "page": 0}]}, other)
    reply = run(manager_tools._build_add_form_link, deliverable_id=page["id"],
                form_id=theirs["id"])
    check("the form must belong to the page's client", "no form" in reply, True)


def test_an_automation_is_applied_after_the_yes():
    proposal, problems = automations.propose(
        ORG, "Welcome sequence",
        [{"id": "note", "type": "notify", "title": "Log it", "next": []}])
    check("the proposal is valid", problems, [])
    reply = run(manager_tools._build_apply_automation, proposal_id=0)
    check("it is applied", "Applied" in reply, True)
    names = [row["name"] for row in automations.list_for_org(ORG)]
    check("and it exists", "Welcome sequence" in names, True)
    again = run(manager_tools._build_apply_automation, proposal_id=0)
    check("a second apply has nothing waiting", "no automation proposal" in again, True)


def test_the_model_is_told_what_exists():
    caps = run(manager_tools._build_describe_capabilities)
    for needle in ("form.submitted", "publish_page", "add_form_link", "apply_automation"):
        check("capabilities name %s" % needle, needle in caps, True)


if __name__ == "__main__":
    ORG = new_org("Extras Client")
    JOB = new_job(ORG)
    test_a_form_is_published_and_gets_a_button()
    test_another_clients_form_is_refused()
    test_an_automation_is_applied_after_the_yes()
    test_the_model_is_told_what_exists()
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nSITE EXTRAS OK - live form, button on the page, automation applied after a yes")
