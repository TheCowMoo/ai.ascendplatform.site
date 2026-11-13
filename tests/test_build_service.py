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

import actions  # noqa: E402
import automations  # noqa: E402
import build_service  # noqa: E402
import builds  # noqa: E402
import db  # noqa: E402
import deliverables  # noqa: E402
import forms as forms_service  # noqa: E402
import jobs  # noqa: E402
import orgs as orgs_service  # noqa: E402
import profile as profile_mod  # noqa: E402
import provisioner  # noqa: E402
import sites as sites_service  # noqa: E402
import templates_service  # noqa: E402
from tools import mail as mail_tool  # noqa: E402

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
    check("the template is a designed HTML email", "<html" in template["body"].lower(), True)
    check("nothing was sent", "nothing has been sent" in
          " ".join(result.get("notes") or []).lower(), True)
    check("a sign-up form exists", bool(result.get("form_id")), True)
    automation = automations.get(result["automation_id"], org)
    check("the welcome automation is switched off", automation["enabled"], 0)


def test_a_newsletter_can_be_a_three_email_series():
    org = orgs_service.create_org({"name": "Build Series"})["id"]
    build = make_build(org, "newsletter", {
        "audience": "Regulars",
        "goal": "Welcome people",
        "subject": "Hello",
        "story": "Here is the news.",
        "tone": "friendly",
        "length": "short",
        "need_form": "yes",
        "form_fields": ["email"],
        "welcome": "yes",
        "welcome_message": "Thanks for joining.",
        "sequence": "series",
        "followup": "Share one useful tip.",
        "cadence": "3_days",
    })
    result = build_service.execute(build["id"])
    check("three welcome templates exist", len(result.get("welcome_template_ids") or []), 3)
    automation = automations.get(result["automation_id"], org)
    check("the series is switched off", automation["enabled"], 0)
    check("it has three approvals and three sends and two waits",
          len(automation["steps"]), 8)
    check("the trigger is a form submission",
          automation["trigger_config"].get("event"), "form.submitted")


def test_a_multi_page_site_publishes_with_one_approval():
    org = orgs_service.create_org({"name": "Build Multi"})["id"]
    docroot = os.path.join(tempfile.mkdtemp(prefix="build-multi-"), "public_html")
    sites_service.create_site(
        {"domain": "multi.test", "docroot": docroot}, org_id=org)
    build = make_build(org, "website", {
        "business_name": "Acme",
        "what_you_do": "Bread and cakes",
        "goal": "contact",
        "services": "Bread\nCakes",
        "email": "hi@acme.test",
        "style": "clean",
        "want_form": "no",
        "want_newsletter": "no",
        "has_domain": "yes",
        "domain_name": "multi.test",
        "site_size": "multi_page",
        "pages": ["about", "services", "contact", "blog"],
    })
    result = build_service.execute(build["id"])
    builds.finish(build["id"], result)
    check("it is a multi-page build", result["mode"], "multi_page")
    check("home plus four pages were made", len(result["page_ids"]), 5)
    home = deliverables.get_deliverable(result["page_ids"][0])
    check("the home page has navigation", "/about.html" in home["content"], True)
    check("a page is still a draft", home["status"], "draft")
    original = actions.is_dry_run
    original_root = provisioner.SITES_ROOT
    actions.is_dry_run = lambda: False
    provisioner.SITES_ROOT = os.path.dirname(docroot)
    try:
        published = build_service.publish_site_build(build["id"], org, "tester")
    finally:
        actions.is_dry_run = original
        provisioner.SITES_ROOT = original_root
    check("every page has a URL", len(published["urls"]), 5)
    for name in ("index.html", "about.html", "services.html", "contact.html", "blog.html"):
        check("%s was written" % name, os.path.exists(os.path.join(docroot, name)), True)
    statuses = [deliverables.get_deliverable(page)["status"]
                for page in result["page_ids"]]
    check("all pages are published", statuses, ["published"] * 5)
    batch = [row for row in actions.list_approvals(limit=20)
             if "build #%d" % build["id"] in (row.get("summary") or "")]
    check("the batch has one approval", len(batch), 1)

    post = deliverables.create_deliverable({
        "kind": "blog", "title": "A new post", "slug": "a-new-post",
        "summary": "Fresh from the bakery", "content": "<html><body>post</body></html>",
        "site_id": sites_service.get_site_by_domain("multi.test", org)["id"],
        "status": "published",
    }, org_id=org)
    actions.is_dry_run = lambda: False
    provisioner.SITES_ROOT = os.path.dirname(docroot)
    try:
        refreshed = build_service.refresh_blog_page(build["id"], org, "tester")
    finally:
        actions.is_dry_run = original
        provisioner.SITES_ROOT = original_root
    check("the blog page refresh reports the post", refreshed["posts"], 1)
    with open(os.path.join(docroot, "blog.html"), encoding="utf-8") as handle:
        check("the index now links the post", "A new post" in handle.read(), True)
    deliverables.delete_deliverable(post["id"])


def test_a_blog_post_publishes_through_the_same_path():
    org = orgs_service.create_org({"name": "Build Blog Publish"})["id"]
    docroot = os.path.join(tempfile.mkdtemp(prefix="build-blog-publish-"), "public_html")
    site = sites_service.create_site({"domain": "blog.test", "docroot": docroot}, org_id=org)
    post = deliverables.create_deliverable({
        "kind": "blog", "title": "A post", "slug": "a-post",
        "summary": "Read this", "content": "<html><body>A post</body></html>",
        "site_id": site["id"],
    }, org_id=org)
    original = actions.is_dry_run
    original_root = provisioner.SITES_ROOT
    actions.is_dry_run = lambda: False
    provisioner.SITES_ROOT = os.path.dirname(docroot)
    try:
        published = deliverables.publish_deliverable(
            post["id"], site_id=site["id"], actor="tester")
    finally:
        actions.is_dry_run = original
        provisioner.SITES_ROOT = original_root
    check("a blog post can be published", published["url"], "https://blog.test/a-post.html")
    check("the file exists", os.path.exists(os.path.join(docroot, "a-post.html")), True)
    check("the post is marked published",
          deliverables.get_deliverable(post["id"])["status"], "published")


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


def test_html_email_is_detected_and_has_a_text_fallback():
    check("an HTML document is recognised",
          mail_tool._looks_html("<!DOCTYPE html><html><body>Hi</body></html>"), True)
    check("plain text is not", mail_tool._looks_html("Hi there"), False)
    check("the text fallback is readable", mail_tool._plain_text(
        "<html><body><h1>Hello</h1><p>World</p></body></html>"), "Hello World")


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
    test_a_newsletter_can_be_a_three_email_series()
    test_a_multi_page_site_publishes_with_one_approval()
    test_a_blog_post_publishes_through_the_same_path()
    test_html_email_is_detected_and_has_a_text_fallback()
    test_the_worker_task_finishes_and_records_the_result()
    test_another_clients_build_is_invisible()
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nBUILD SERVICE OK - drafts, forms and templates, no publish and no send")
