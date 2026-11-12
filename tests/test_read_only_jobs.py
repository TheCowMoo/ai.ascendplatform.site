"""A review job must not be able to overwrite the page it was asked to inspect.

Run: .\\venv\\Scripts\\python.exe tests\\test_read_only_jobs.py

This replays job #167: a published Studio page, a QA prompt that says "do not redesign or
republish", and a child team that saved its report over draft #33. The assertions are the
four locks that make the replay impossible: the job flag, the read-only tool list, the
team instructions, and the tool-level refusal.
"""

import asyncio
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="read-only-"), "test.db").replace("\\", "/")
os.environ.pop("ALLOW_LIVE_ACTIONS", None)
os.environ.pop("JOB_BACKEND", None)
os.environ.pop("REDIS_URL", None)

from autogen_core import CancellationToken  # noqa: E402

import app as appmod  # noqa: E402,F401
import agents  # noqa: E402
import db  # noqa: E402
import deliverables  # noqa: E402
import intent  # noqa: E402
import jobs  # noqa: E402
import manager  # noqa: E402
import orgs as orgs_service  # noqa: E402
import studio  # noqa: E402
import tools as tool_registry  # noqa: E402
import tools.site as site_tools  # noqa: E402

db.init_db()
DRY = CancellationToken()
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


def org(name):
    return orgs_service.create_org({"name": name})["id"]


def draft(org_id, title="Cow Barn Tour", content="<h1>Cow Barn Tour</h1>"):
    return deliverables.create_deliverable(
        {"kind": "website", "title": title, "content": content, "slug": "cow-barn-tour-home"},
        org_id=org_id)


def start(parent, **kwargs):
    tool = tool_registry.resolve(["start_job"], job_id=parent)[0]
    return asyncio.run(tool.run_json(kwargs, DRY))


def test_the_qa_replay_is_read_only():
    org_id = org("Read Only QA")
    item = draft(org_id)
    parent = jobs.create_job("Studio QA", "[Studio context ...]", user_id(), "selector",
                             "chat", org_id=org_id, deliverable_id=item["id"])
    original_submit = jobs.submit
    jobs.submit = lambda job_id: None
    try:
        reply = start(
            parent,
            prompt=("Verify that the published page is rendering correctly. Do not "
                    "redesign or republish the page - report a short list of anything wrong."),
            deliverable_id=item["id"],
        )
    finally:
        jobs.submit = original_submit
    check("the manager says the child is read-only", "read-only" in reply, True)
    child = jobs.list_jobs(org_id=org_id)[0]
    check("the child job carries the flag", bool(child.get("read_only")), True)
    check("its prompt has no save instruction",
          "Save the finished document" in (child.get("prompt") or ""), False)
    check("its prompt is explicitly read-only",
          "READ-ONLY REVIEW" in (child.get("prompt") or ""), True)
    check("it still names the draft for context",
          "draft #%d" % item["id"] in (child.get("prompt") or ""), True)
    check("the parent is not read-only", bool(jobs.get_job(parent).get("read_only")), False)

    save = tool_registry.resolve(["save_deliverable"], job_id=child["id"])[0]
    saved = asyncio.run(save.run_json(
        {"kind": "website", "title": "QA report",
         "content": "<h1>QA report</h1><p>Everything passed.</p>"}, DRY))
    check("save_deliverable refuses", "read-only" in saved.lower(), True)
    stored = deliverables.get_deliverable(item["id"])
    check("the page content is unchanged", stored["content"], "<h1>Cow Barn Tour</h1>")
    check("and it is still a draft", stored["status"], "draft")

    publish = site_tools._build_publish_page(None, job_id=child["id"])
    refused = asyncio.run(publish(deliverable_id=item["id"]))
    check("publish_page refuses", "read-only" in refused.lower(), True)

    grants = agents.read_only_grants(["save_deliverable", "publish_page", "provision_site",
                                      "send_email", "fetch_page", "list_sites"])
    check("the team keeps only read tools", grants, ["fetch_page", "list_sites"])
    operator = next(row for row in agents.DEFAULT_ROSTER if row["name"] == "Operator")
    operator_grants = agents.read_only_grants(operator["tools"])
    for forbidden in ("save_deliverable", "publish_page", "provision_site",
                      "write_site_content", "send_email", "import_photo_to_media"):
        check("Operator loses %s" % forbidden, forbidden in operator_grants, False)
    check("Operator keeps fetch_page", "fetch_page" in operator_grants, True)
    message = agents.system_message_for(operator, read_only=True)
    check("the read-only rule is in the instructions",
          agents.READ_ONLY_RULE.strip() in message, True)
    check("the save rule is not", "finish by calling save_deliverable" in message, False)

    manager_grants = manager.granted_for({"read_only": 1})
    check("the Manager loses save_deliverable", "save_deliverable" in manager_grants, False)
    check("the Manager loses publish_page", "publish_page" in manager_grants, False)
    check("the Manager keeps fetch_page", "fetch_page" in manager_grants, True)
    check("the Manager keeps start_job", "start_job" in manager_grants, True)
    check("an edit turn keeps save_deliverable",
          "save_deliverable" in manager.granted_for({"read_only": 0}), True)

    block = studio.deliverable_block(item)
    check("the Studio block offers the edit path", "save_deliverable" in block, True)
    check("and names the read-only path", "READ-ONLY" in block, True)
    check("the Manager prompt names the read-only rule", "READ-ONLY" in manager.SYSTEM, True)


def test_an_edit_job_still_saves_and_keeps_a_version():
    org_id = org("Edit Still Works")
    item = draft(org_id, content="<h1>old</h1>")
    parent = jobs.create_job("Studio edit", "make it warmer", user_id(), "selector",
                             "chat", org_id=org_id, deliverable_id=item["id"])
    original_submit = jobs.submit
    jobs.submit = lambda job_id: None
    try:
        reply = start(parent, prompt="change the headline to 'Meet the herd'",
                      deliverable_id=item["id"])
    finally:
        jobs.submit = original_submit
    check("an edit is not read-only", "read-only" in reply.lower(), False)
    child = jobs.list_jobs(org_id=org_id)[0]
    check("the edit child is not flagged", bool(child.get("read_only")), False)
    check("the edit prompt keeps the save instruction",
          "Save the finished document" in (child.get("prompt") or ""), True)
    save = tool_registry.resolve(["save_deliverable"], job_id=child["id"])[0]
    saved = asyncio.run(save.run_json(
        {"kind": "website", "title": "x", "content": "<h1>new</h1>"}, DRY))
    check("the edit saves", saved.startswith("Updated"), True)
    check("the page changed", deliverables.get_deliverable(item["id"])["content"],
          "<h1>new</h1>")
    versions = deliverables.versions(item["id"])
    check("the old content has a version",
          any(version["bytes"] == len("<h1>old</h1>") for version in versions), True)


def test_published_edits_return_to_draft():
    org_id = org("Published Draft")
    item = draft(org_id, content="<h1>live</h1>")
    deliverables.update_deliverable(item["id"], {"status": "published",
                                                 "url": "https://example.test/"})
    updated = deliverables.update_deliverable(item["id"], {"content": "<h1>report</h1>"})
    check("a content change makes a published page a draft", updated["status"], "draft")
    check("the version list has the live content",
          any(version["bytes"] == len("<h1>live</h1>")
              for version in deliverables.versions(item["id"])), True)


def test_intent_only_calls_a_review_a_review():
    check("QA check", intent.looks_read_only("QA check live page: Cow Barn Tour"), True)
    check("do not republish", intent.looks_read_only("Do not redesign or republish the page"), True)
    check("read-only label", intent.looks_read_only("This is a read-only review"), True)
    check("review plus fix stays an edit",
          intent.looks_read_only("review the page and fix the typos"), False)
    check("a build request is an edit", intent.looks_read_only("shorten the headline"), False)
    check("publish is a write", intent.looks_read_only("publish it as the home page"), False)


def test_the_parent_read_only_is_inherited():
    org_id = org("Read Only Parent")
    item = draft(org_id)
    parent = jobs.create_job("Review", "review", user_id(), "selector", "chat",
                             org_id=org_id, deliverable_id=item["id"], read_only=True)
    original_submit = jobs.submit
    jobs.submit = lambda job_id: None
    try:
        reply = start(parent, prompt="change the headline", deliverable_id=item["id"])
    finally:
        jobs.submit = original_submit
    child = jobs.list_jobs(org_id=org_id)[0]
    check("a read-only parent keeps the child read-only", bool(child.get("read_only")), True)
    check("and says so", "read-only" in reply.lower(), True)


if __name__ == "__main__":
    try:
        test_the_qa_replay_is_read_only()
        test_an_edit_job_still_saves_and_keeps_a_version()
        test_published_edits_return_to_draft()
        test_intent_only_calls_a_review_a_review()
        test_the_parent_read_only_is_inherited()
    finally:
        if FAILED:
            print("\n%d FAILED" % len(FAILED))
            raise SystemExit(1)
        print("\nREAD-ONLY JOBS OK - a review cannot save, publish or overwrite the page")


