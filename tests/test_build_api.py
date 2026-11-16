"""The guided-build API: tenant-scoped, resumable, and never starts without answers.

Run: .\\venv\\Scripts\\python.exe tests\\test_build_api.py
"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="build-api-"), "test.db").replace("\\", "/")
os.environ.pop("ALLOW_LIVE_ACTIONS", None)
os.environ.pop("JOB_BACKEND", None)
os.environ.pop("REDIS_URL", None)

import app as appmod  # noqa: E402
import builds  # noqa: E402
import db  # noqa: E402
import forms as forms_service  # noqa: E402
import jobs  # noqa: E402
import orgs as orgs_service  # noqa: E402
import sites as sites_service  # noqa: E402
import templates_service  # noqa: E402

db.init_db()
FAILED = []


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


def client(org):
    c = appmod.app.test_client()
    login = c.post("/api/auth/login", json={"email": "admin@example.com",
                                            "password": "admin123"})
    assert login.status_code == 200, login.get_data(as_text=True)
    c.post("/api/session/org", json={"org_id": org})
    return c


def test_requires_login_and_a_client():
    anon = appmod.app.test_client()
    check("creating a build needs a login", anon.post("/api/builds", json={"kind": "website"}).status_code, 401)
    admin = appmod.app.test_client()
    admin.post("/api/auth/login", json={"email": "admin@example.com", "password": "admin123"})
    check("creating a build needs a client picked",
          admin.post("/api/builds", json={"kind": "website"}).status_code, 400)
    check("an unknown kind is refused",
          admin.post("/api/builds", json={"kind": "spaceship"}).status_code, 400)


def test_the_interview_advances_and_a_job_starts():
    org = orgs_service.create_org({"name": "API Build"})["id"]
    c = client(org)
    made = c.post("/api/builds", json={"kind": "website"})
    check("a build is created", made.status_code, 201)
    build = made.get_json()["build"]
    build_id = build["id"]
    check("it starts at the first step", build["step"], 0)
    check("it has questions", len(build["questions"]) > 0, True)
    missing = c.post("/api/builds/%d/answers" % build_id,
                     json={"step": 0, "answers": {}})
    check("required answers are refused", missing.status_code, 400)
    check("with a readable message", "business" in missing.get_json()["error"].lower(), True)

    first = c.post("/api/builds/%d/answers" % build_id, json={
        "step": 0,
        "answers": {"business_name": "Acme", "what_you_do": "Bread"},
    }).get_json()["build"]
    check("the first step is saved", first["answers"]["business_name"], "Acme")
    check("the next step is shown", first["step"], 1)

    # The defaults make the optional steps safe to skip.
    current = first
    for step in (1, 2, 3):
        payload = c.post("/api/builds/%d/answers" % build_id,
                         json={"step": step, "answers": {}}).get_json()
        current = payload.get("build") or current
    check("the review is ready", current["ready"], True)
    check("no questions remain", current["questions"], [])

    original_submit = jobs.submit
    jobs.submit = lambda job_id: "local"   # keep the test to the API contract
    try:
        started = c.post("/api/builds/%d/build" % build_id)
    finally:
        jobs.submit = original_submit
    check("the build job starts", started.status_code, 201)
    job_id = started.get_json()["job_id"]
    check("the job exists", jobs.get_job(job_id) is not None, True)
    check("the build is marked building",
          builds._row(build_id)["status"], "building")


def test_a_designed_template_preview_is_not_escaped():
    org = orgs_service.create_org({"name": "API Template"})["id"]
    html = templates_service.save_template({
        "name": "Newsletter", "subject": "Hello",
        "body": "<!DOCTYPE html><html><body><h1>Hi there</h1></body></html>",
    }, org)
    plain = templates_service.save_template({
        "name": "Plain", "subject": "Plain", "body": "Hello <b>world</b>",
    }, org)
    c = client(org)
    designed = c.get("/api/templates/%d/preview" % html["id"]).get_json()
    check("HTML is kept as a document", "<h1>Hi there</h1>" in designed["html"], True)
    legacy = c.get("/api/templates/%d/preview" % plain["id"]).get_json()
    check("plain template text is still escaped", "&lt;b&gt;world&lt;/b&gt;" in legacy["html"], True)


def test_a_builds_form_link_follows_the_domain_as_it_goes_live():
    """A completed build must not freeze the platform URL in its result.

    The website build often creates the site before it is live, so the form link it
    stores is the safe platform fallback. Once the domain is live, every consumer of
    the build (the card, the Manager) has to read the same live link as the Forms tab.
    """
    org = orgs_service.create_org({"name": "API Form Links"})["id"]
    form = forms_service.create_form({
        "name": "Build callback", "kind": "form",
        "questions": [{"id": "email", "label": "Email", "type": "email",
                       "page": 0, "required": True}],
    }, org)
    form = forms_service.update_form(form["id"], org, {"status": "published"})
    site = sites_service.create_site({"domain": "build-links.test"}, org_id=org)
    platform = forms_service.public_url(form)
    slug = form["slug"]

    build = builds.create(org, 0, "website", {"business_name": "Links"})
    builds.finish(build["id"], {
        "kind": "website",
        "form_id": form["id"],
        "form_url": platform,
        "form_links": [{"label": "Contact us", "form_id": form["id"],
                        "slug": slug, "url": "/f/" + slug,
                        "public_url": platform}],
    })
    c = client(org)
    before = c.get("/api/builds/%d" % build["id"]).get_json()["build"]
    check("a build made before the domain is live keeps the platform link",
          before["result"]["form_url"], platform)

    sites_service.update_site(site["id"], {"status": "live"})
    after = c.get("/api/builds/%d" % build["id"]).get_json()["build"]
    live = "https://build-links.test/f/" + slug
    check("the build's form URL follows the live domain", after["result"]["form_url"], live)
    check("and so does its form button",
          after["result"]["form_links"][0]["public_url"], live)

    # Results written before form_id/slug metadata existed still stored a usable URL.
    legacy = builds.create(org, 0, "form", {"purpose": "leads"})
    builds.finish(legacy["id"], {
        "kind": "form", "form_url": platform,
        "form_links": [{"label": "Old button", "url": "/f/" + slug,
                        "public_url": platform}],
    })
    old = c.get("/api/builds/%d" % legacy["id"]).get_json()["build"]
    check("a legacy result is resolved by its stored slug",
          old["result"]["form_url"], live)
    check("a legacy form button is resolved too",
          old["result"]["form_links"][0]["public_url"], live)


def test_another_client_cannot_see_it():
    first = orgs_service.create_org({"name": "API First"})["id"]
    second = orgs_service.create_org({"name": "API Second"})["id"]
    c = client(first)
    build_id = c.post("/api/builds", json={"kind": "form"}).get_json()["build"]["id"]
    check("the owner can read it", c.get("/api/builds/%d" % build_id).status_code, 200)
    c.post("/api/session/org", json={"org_id": second})
    check("another client gets a 404", c.get("/api/builds/%d" % build_id).status_code, 404)


if __name__ == "__main__":
    test_requires_login_and_a_client()
    test_the_interview_advances_and_a_job_starts()
    test_a_designed_template_preview_is_not_escaped()
    test_a_builds_form_link_follows_the_domain_as_it_goes_live()
    test_another_client_cannot_see_it()
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nBUILD API OK - plain answers in, a tracked job out")