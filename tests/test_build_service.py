"""A guided build produces drafts and reusable pieces - never a publish or a send.

Run: .\\venv\\Scripts\\python.exe tests\\test_build_service.py

The executor is deliberately deterministic. These checks use a throwaway database and
exercise the same services the console uses, so a broken template, form mapping or
automation draft is caught without a browser or a model.
"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="build-service-"), "test.db").replace("\\", "/")
os.environ.pop("ALLOW_LIVE_ACTIONS", None)
os.environ.pop("JOB_BACKEND", None)
os.environ.pop("REDIS_URL", None)

import automations  # noqa: E402
import build_service  # noqa: E402
import builds  # noqa: E402
import db  # noqa: E402
import deliverables  # noqa: E402
import forms as forms_service  # noqa: E402
import jobs  # noqa: E402
import orgs as orgs_service  # noqa: E402
import profile as profile_mod  # noqa: E402
import templates_service  # noqa: E402

db.init_db()
FAILED = []


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


def make_build(org, kind, answers):
    return builds.create(org, 0, kind, answers)


def test_a_website_becomes_a_draft_and_a_form():
    org = orgs_service.create_org({"name": "Build Bakery"})["id"]
    profile_mod.update_profile(org, {"name": "Old name", "brand_color": "#111111"})
    build = make_build(org, "website", {
        "business_name": "Acme Bakery",
        "what_you_do": "We bake fresh bread.",
        "goal": "contact",
        "services": "Bread\nCakes",
        "email": "hello@acme.test",
        "style": "warm",
        "brand_color": "#123456",
        "want_form": "yes",
        "want_newsletter": "no",
        "has_domain": "no",
    })
    result = build_service.execute(build["id"])
    check("the kind is reported", result["kind"], "website")
    check("there is a website draft", bool(result.get("deliverable_id")), True)
    item = deliverables.get_deliverable(result["deliverable_id"])
    check("it is a draft", item["status"], "draft")
    check("it carries the business name", "Acme Bakery" in item["content"], True)
    check("it uses the chosen colour", "#123456" in item["content"], True)
    check("no site was invented", result.get("site_id"), None)
    check("a contact form exists", len(result.get("form_ids") or []), 1)
    form = forms_service.get_form(result["form_ids"][0], org)
    check("the form is live at its link", form["status"], "published")
    check("its link is in the build result", bool(result.get("form_links")), True)
    check("the profile was updated for later work",
          profile_mod.get_profile(org)["name"], "Acme Bakery")


def test_a_form_builds_a_working_form_and_a_switched_off_welcome():
    org = orgs_service.create_org({"name": "Build Forms"})["id"]
    build = make_build(org, "form", {
        "purpose": "newsletter",
        "fields": ["name", "email"],
        "success_message": "You are in.",
        "button_label": "Join",
        "place": "link",
        "welcome_email": "yes",
        "welcome_subject": "Welcome",
        "welcome_message": "Thanks for joining.",
        "brand_color": "#0ea5e9",
    })
    result = build_service.execute(build["id"])
    form = forms_service.get_form(result["form_id"], org)
    check("the form is published", form["status"], "published")
    check("the success message travels", form["settings"]["success"], "You are in.")
    check("a shareable link is returned", bool(result.get("form_url")), True)
    check("a welcome template was saved", bool(result.get("template_id")), True)
    template = templates_service.get_template(result["template_id"], org)
    check("with the requested subject", template["subject"], "Welcome")
    automation = automations.get(result["automation_id"], org)
    check("the automation exists", bool(automation), True)
    check("and is switched off", automation["enabled"], 0)
    check("with a form trigger", automation["trigger_type"], "event")
    check("listening for submissions",
          automation["trigger_config"].get("event"), "form.submitted")


def test_a_blog_post_is_a_draft_and_can_be_published_later():
    org = orgs_service.create_org({"name": "Build Blog"})["id"]
    build = make_build(org, "blog", {
        "topic": "How to choose a cake",
        "audience": "Parents",
        "takeaway": "Pick the size and flavour first.",
        "points": "Count the guests\nChoose a flavour",
        "tone": "friendly",
        "length": "short",
        "cta": "none",
    })
    result = build_service.execute(build["id"])
    item = deliverables.get_deliverable(result["deliverable_id"])
    check("the post is a blog draft", item["kind"], "blog")
    check("it is not published", item["status"], "draft")
    check("the article carries the topic", "choose a cake" in item["content"].lower(), True)
    check("it has no invented site", result.get("site_id"), None)


def test_a_newsletter_builds_a_template_and_signup_without_sending():
    org = orgs_service.create_org({"name": "Build Newsletter"})["id"]
    build = make_build(org, "newsletter", {
        "audience": "Regulars",
        "goal": "Share the weekly loaf",
        "subject": "This week at the bakery",
        "story": "The sourdough is back.",
        "tone": "friendly",
        "length": "short",
        "need_form": "yes",
        "form_fields": ["name", "email"],
        "welcome": "yes",
        "welcome_message": "Thanks for joining the list.",
    })
    result = build_service.execute(build["id"])
    template = templates_service.get_template(result["template_id"], org)
    check("a newsletter template exists", bool(template), True)
    check("with the subject", template["subject"], "This week at the bakery")
    check("nothing was sent", "nothing has been sent" in
          " ".join(result.get("notes") or []).lower(), True)
    check("a sign-up form exists", bool(result.get("form_id")), True)
    automation = automations.get(result["automation_id"], org)
    check("the welcome automation is switched off", automation["enabled"], 0)


def test_the_worker_task_finishes_and_records_the_result():
    org = orgs_service.create_org({"name": "Build Worker"})["id"]
    build = make_build(org, "blog", {"topic": "Worker post", "takeaway": "It runs."})
    job_id = jobs.create_job("Build", "guided build", 0, "selector", "build", org_id=org)
    builds.mark_building(build["id"], job_id)
    events = []
    result = build_service.build_task(
        jobs.get_job(job_id), lambda source, kind, content: events.append((source, kind, content)))
    stored = builds._row(build["id"])
    check("the task reports success", "Built" in result, True)
    check("the build is done", stored["status"], "done")
    check("its result names the draft", bool(builds._json(stored["result_json"], {}).get(
        "deliverable_id")), True)
    check("the event log carries progress",
          any(kind == "step" for source, kind, content in events), True)


def test_another_clients_build_is_invisible():
    first = orgs_service.create_org({"name": "Build First"})["id"]
    second = orgs_service.create_org({"name": "Build Second"})["id"]
    build = make_build(first, "blog", {"topic": "Private", "takeaway": "Mine"})
    check("the owner can read it", bool(builds.get(build["id"], first)), True)
    check("another client cannot", builds.get(build["id"], second), None)


if __name__ == "__main__":
    test_a_website_becomes_a_draft_and_a_form()
    test_a_form_builds_a_working_form_and_a_switched_off_welcome()
    test_a_blog_post_is_a_draft_and_can_be_published_later()
    test_a_newsletter_builds_a_template_and_signup_without_sending()
    test_the_worker_task_finishes_and_records_the_result()
    test_another_clients_build_is_invisible()
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nBUILD SERVICE OK - drafts, forms and templates, no publish and no send")
