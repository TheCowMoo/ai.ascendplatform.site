"""Chat assistant tools and the widget that makes one visible on a site.

Run: .\\venv\\Scripts\\python.exe tests\\test_chat_tools.py

The missing feature had two halves: no agent tools, and a ``linked_bot_id`` that
nothing rendered. These checks cover both, plus the cross-tenant rule that a site
may only embed its own client's bot.
"""

import asyncio
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="chat-tools-"), "test.db").replace("\\", "/")
os.environ["SCHEDULER_ENABLED"] = "0"
os.environ["PLATFORM_URL"] = "https://platform.test"
os.environ.pop("ALLOW_LIVE_ACTIONS", None)
os.environ.pop("JOB_BACKEND", None)
os.environ.pop("REDIS_URL", None)

import actions  # noqa: E402
import app as appmod  # noqa: E402,F401
import bots as bots_service  # noqa: E402
import db  # noqa: E402
import deliverables  # noqa: E402
import forms as forms_service  # noqa: E402
import jobs  # noqa: E402
import orgs as orgs_service  # noqa: E402
import provisioner  # noqa: E402
import sites as sites_service  # noqa: E402
import user_service  # noqa: E402
import widget_embed  # noqa: E402
import tools.chat as chat_tools  # noqa: E402

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
    return jobs.create_job("Chat test", "test", requester or admin_id(),
                           "selector", "chat", org_id=org_id)


def run(builder, job_id, **kwargs):
    return asyncio.run(builder(None, job_id=job_id)(**kwargs))


def make_form(org_id):
    form = forms_service.create_form({
        "name": "Enquiry", "kind": "form",
        "questions": [{"id": "email", "label": "Email", "type": "email",
                       "page": 0, "required": True}],
    }, org_id)
    return forms_service.update_form(form["id"], org_id, {"status": "published"})


def test_create_read_update_and_attach():
    org_id = new_org("Chat Client")
    form = make_form(org_id)
    job = new_job(org_id)
    reply = run(chat_tools._build_create_chat_assistant, job,
                name="Trip helper", assistant_name="Marta",
                welcome_message="Hi - ask me about dates, group size or price.",
                quick_replies=json.dumps(["What dates?", "Group size"]),
                knowledge_base="Dates: June and September. Groups: 2-8. Price: 900.",
                brand_color="#123456")
    check("the assistant is created", "Created chat assistant" in reply, True)
    check("the reply carries the widget snippet", "data-bot=" in reply, True)
    check("the reply points to the enquiry form", "/f/" in reply, True)

    rows = bots_service.list_bots(org_id)
    check("one assistant exists", len(rows), 1)
    bot = rows[0]
    check("branding is stored", bot["brand_color"], "#123456")
    check("quick replies are parsed", bot["quick_replies"], ["What dates?", "Group size"])
    check("the fallback form is in the prompt", form["slug"] in bot["system_prompt"], True)

    listing = json.loads(run(chat_tools._build_list_chat_assistants, job))
    check("the list names it", listing[0]["name"], "Trip helper")
    check("the list hides the webhook", "webhook_url" in json.dumps(listing), False)
    detail = json.loads(run(chat_tools._build_read_chat_assistant, job,
                            bot_id=bot["id"]))
    check("read returns the knowledge", "900" in detail["knowledge_base"], True)
    check("read never returns a webhook", "webhook_url" in detail, False)

    changed = run(chat_tools._build_update_chat_assistant, job, bot_id=bot["id"],
                  welcome_message="Hello! How can I help?")
    check("update saves the copy", "Updated chat assistant" in changed, True)
    check("the public copy changed",
          bots_service.get_bot(bot["id"], org_id)["welcome_message"],
          "Hello! How can I help?")

    duplicate = run(chat_tools._build_create_chat_assistant, job, name="Trip helper")
    check("a duplicate name is refused", "already exists" in duplicate, True)

    site = sites_service.create_site({"domain": "chat.test", "target": "local"},
                                     org_id=org_id)
    attached = run(chat_tools._build_attach_chat_assistant, job,
                   site_id=site["id"], bot_id=bot["id"])
    check("attach links the bot in dry-run", "Linked" in attached, True)
    check("the site row now names the bot",
          sites_service.get_site(site["id"])["linked_bot_id"], bot["id"])
    check("attach is idempotent", "Linked" in run(
        chat_tools._build_attach_chat_assistant, job, site_id=site["id"],
        bot_id=bot["id"]), True)


def test_cross_tenant_bots_and_member_writes_are_refused():
    org_id = new_org("Chat Owner")
    other = new_org("Chat Other")
    run(chat_tools._build_create_chat_assistant, new_job(org_id), name="Mine")
    run(chat_tools._build_create_chat_assistant, new_job(other), name="Theirs")
    mine = bots_service.list_bots(org_id)[0]
    other_bot = bots_service.list_bots(other)[0]

    try:
        sites_service.create_site({"domain": "cross-bot.test",
                                   "linked_bot_id": other_bot["id"]}, org_id=org_id)
        raised = False
    except sites_service.SiteError as exc:
        raised = "another client" in str(exc)
    check("a site cannot link another client's bot", raised, True)

    site = sites_service.create_site({"domain": "cross-site.test"}, org_id=org_id)
    crossed = run(chat_tools._build_attach_chat_assistant, new_job(org_id),
                  site_id=site["id"], bot_id=other_bot["id"])
    check("attach refuses another client's bot", "no chat assistant" in crossed, True)
    check("an unknown site is refused", "no site" in run(
        chat_tools._build_attach_chat_assistant, new_job(org_id),
        site_id=999999, bot_id=mine["id"]), True)

    member = user_service.create_user({
        "email": "member-chat@example.test", "name": "Member",
        "password": "member-pass-123", "role": "member", "org_id": org_id,
    }, admin_id(), True, None)
    denied = run(chat_tools._build_create_chat_assistant,
                 new_job(org_id, requester=member["id"]), name="Nope")
    check("a member cannot create an assistant", "Only a client admin" in denied, True)


def test_the_widget_is_injected_and_not_duplicated():
    tag = widget_embed.snippet("abc12345")
    check("the snippet points at the platform", "https://platform.test/widget.js" in tag, True)
    page = "<html><body><h1>Hi</h1></body></html>"
    once = widget_embed.inject(page, "abc12345")
    check("the widget lands before body close", "</script>\n</body>" in once, True)
    check("injecting twice changes nothing", widget_embed.inject(once, "abc12345"), once)
    changed = widget_embed.inject(once, "other1234")
    check("changing the linked bot replaces the launcher",
          "abc12345" not in changed and "other1234" in changed, True)
    check("and leaves one launcher", changed.count("data-bot="), 1)
    check("an invalid bot id is ignored", widget_embed.inject(page, "bad id"), page)
    check("a bodyless fragment gets the widget appended",
          "data-bot=" in widget_embed.inject("<p>fragment</p>", "abc12345"), True)
    check("unlinking removes the launcher",
          "data-bot=" not in widget_embed.strip(once), True)


def test_publishing_injects_the_linked_widget():
    org_id = new_org("Widget Publisher")
    bot = bots_service.create_bot({"name": "Site helper"}, org_id)
    docroot = tempfile.mkdtemp(prefix="widget-site-")
    site = sites_service.create_site({
        "domain": "widget-publish.test", "docroot": docroot,
        "linked_bot_id": bot["id"],
    }, org_id=org_id)
    item = deliverables.create_deliverable({
        "kind": "website", "title": "Home", "slug": "home",
        "content": "<html><body><h1>Hello</h1></body></html>",
    }, org_id=org_id)
    original = actions.is_dry_run
    original_root = provisioner.SITES_ROOT
    actions.is_dry_run = lambda: False
    provisioner.SITES_ROOT = docroot
    try:
        result = deliverables.publish_deliverable(
            item["id"], site_id=site["id"], as_home=True, preapproved=True)
    finally:
        actions.is_dry_run = original
        provisioner.SITES_ROOT = original_root
    check("the publish succeeded", result.get("ok"), True)
    path = os.path.join(docroot, "index.html")
    with open(path, encoding="utf-8") as handle:
        body = handle.read()
    check("the live page carries the bot id", 'data-bot="%s"' % bot["id"] in body, True)
    check("the page still has its content", "<h1>Hello</h1>" in body, True)


if __name__ == "__main__":
    test_create_read_update_and_attach()
    test_cross_tenant_bots_and_member_writes_are_refused()
    test_the_widget_is_injected_and_not_duplicated()
    test_publishing_injects_the_linked_widget()
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nCHAT TOOLS OK - create, edit, attach and render the widget")
