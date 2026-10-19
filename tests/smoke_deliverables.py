"""Deliverables: what the team writes must land in the console, not just the chat.

Run: .\\venv\\Scripts\\python.exe tests\\smoke_deliverables.py

"Create me an email" used to produce words in the job transcript and nothing in the
Emails tab, because no tool wrote to ``deliverables``. This covers the tool that fixes
that - including the ownership rule, which comes from the job row rather than from the
model - and the publish path for website pages.
"""

import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Dry-run: publishing must never touch a real docroot during a test.
os.environ.pop("ALLOW_LIVE_ACTIONS", None)

from autogen_core import CancellationToken  # noqa: E402

import agents  # noqa: E402
import app as appmod  # noqa: E402
import db  # noqa: E402
import deliverables  # noqa: E402
import jobs  # noqa: E402
import orgs as orgs_service  # noqa: E402
import sites as sites_service  # noqa: E402
import tools as tool_registry  # noqa: E402

DRY = CancellationToken()

# Every client and domain gets a per-run suffix. A suite that only passes against a
# fresh database is a suite nobody runs twice.
TOKEN = uuid.uuid4().hex[:6]


def _org(name):
    return orgs_service.create_org({"name": "%s %s" % (name, TOKEN)})


def _site(name, org_id):
    return sites_service.create_site({"domain": "%s-%s.test" % (name, TOKEN)}, org_id=org_id)


def _job_in_org(org_id):
    job_id = jobs.create_job("deliverable check", "write an email", None, "selector", "agent_team")
    with db.get_connection() as conn:
        conn.execute("UPDATE jobs SET org_id = ? WHERE id = ?", (org_id, job_id))
    return job_id


def _save_tool(job_id):
    resolved = tool_registry.resolve(["save_deliverable"], job_id=job_id)
    assert len(resolved) == 1, resolved
    return resolved[0]


def test_tool_saves_a_draft_in_the_jobs_org():
    """The tool writes into the client the job belongs to - not where the model says."""
    org = _org("Deliverable Tool Client")
    job_id = _job_in_org(org["id"])
    reply = asyncio.run(
        _save_tool(job_id).run_json(
            {
                "kind": "email",
                "title": "March offer",
                "content": "<html><body><p>Hello</p></body></html>",
                "summary": "Launch email",
            },
            DRY,
        )
    )
    print("tool reply:", reply)
    assert "Emails" in reply, reply

    rows = deliverables.list_deliverables("email", org["id"])
    assert len(rows) == 1, rows
    item = rows[0]
    assert item["title"] == "March offer"
    assert item["status"] == "draft"
    assert item["job_id"] == job_id
    assert "<html>" in item["content"]

    # A different client must not see it.
    other = _org("Deliverable Tool Other")
    assert deliverables.list_deliverables("email", other["id"]) == []
    print("draft saved to org %s, invisible to org %s" % (org["id"], other["id"]))


def test_the_tool_refuses_an_unknown_kind():
    org = _org("Deliverable Kind Client")
    job_id = _job_in_org(org["id"])
    reply = asyncio.run(
        _save_tool(job_id).run_json(
            {"kind": "invoice", "title": "Nope", "content": "x", "summary": ""}, DRY
        )
    )
    print("refusal:", reply)
    assert "Could not save" in reply, reply
    assert "kind must be one of" in reply, reply
    assert deliverables.list_deliverables(None, org["id"]) == []
    print("unknown kind refused, nothing written")


def test_content_rule_travels_with_the_tool():
    """The 'save what you write' rule is added in code, for existing rosters too."""
    with_tool = {"system_prompt": "You write.", "role": "", "tools": ["save_deliverable"]}
    message = agents.system_message_for(with_tool)
    assert message.startswith("You write."), message
    assert agents.CONTENT_RULE.strip() in message

    without = {"system_prompt": "You write.", "role": "", "tools": ["save_note"]}
    assert agents.system_message_for(without) == "You write."
    print("house rule attached only to agents holding save_deliverable")


def test_page_paths():
    assert deliverables.page_path("") == "index.html"
    assert deliverables.page_path("home") == "index.html"
    assert deliverables.page_path("About Us") == "about-us.html"
    print("page paths OK")



def test_publish_needs_a_site():
    org = _org("Deliverable No Site Client")
    page = deliverables.create_deliverable(
        {"kind": "website", "title": "Landing", "content": "<html></html>"}, org_id=org["id"]
    )
    try:
        deliverables.publish_deliverable(page["id"])
    except deliverables.DeliverableError as exc:
        assert "no site" in str(exc).lower(), exc
        print("no site ->", exc)
    else:
        raise AssertionError("publishing without a site must be refused")


def test_publish_is_a_dry_run_and_leaves_the_page_a_draft():
    org = _org("Deliverable Publish Client")
    _site("publish", org["id"])
    page = deliverables.create_deliverable(
        {"kind": "website", "title": "About", "slug": "about", "content": "<html>hi</html>"},
        org_id=org["id"],
    )
    result = deliverables.publish_deliverable(page["id"])
    print("publish ->", result["dry_run"], result["path"])
    assert result["dry_run"] is True, result
    assert result["path"].endswith("about.html"), result
    assert deliverables.get_deliverable(page["id"])["status"] == "draft"
    print("dry-run publish wrote nothing and left the page a draft")


def test_a_live_page_cannot_be_overwritten_by_slug():
    org = _org("Deliverable Conflict Client")
    _site("conflict", org["id"])
    first = deliverables.create_deliverable(
        {"kind": "website", "title": "Pricing", "slug": "pricing", "content": "<html>1</html>"},
        org_id=org["id"],
    )
    deliverables.update_deliverable(first["id"], {"status": "published"})
    second = deliverables.create_deliverable(
        {"kind": "website", "title": "Pricing v2", "slug": "pricing", "content": "<html>2</html>"},
        org_id=org["id"],
    )
    try:
        deliverables.publish_deliverable(second["id"])
    except deliverables.DeliverableError as exc:
        assert "already published" in str(exc), exc
        print("slug conflict ->", exc)
    else:
        raise AssertionError("a second page must not overwrite a live one silently")


def test_only_website_pages_can_be_published():
    org = _org("Deliverable Kind Guard Client")
    _site("kind", org["id"])
    email = deliverables.create_deliverable(
        {"kind": "email", "title": "Newsletter", "content": "<html>x</html>"}, org_id=org["id"]
    )
    try:
        deliverables.publish_deliverable(email["id"])
    except deliverables.DeliverableError as exc:
        assert "website" in str(exc), exc
        print("email refused ->", exc)
    else:
        raise AssertionError("an email is not a page and must not be pushed to a site")


def test_api_offers_the_website_kind():
    client = appmod.app.test_client()
    resp = client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "admin123"}
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = client.get("/api/deliverables").get_json()
    assert "website" in data["kinds"], data["kinds"]
    assert data["counts"].get("website") is not None, data["counts"]
    print("API kinds:", data["kinds"])


def test_publish_route_validates_the_kind_and_the_session():
    client = appmod.app.test_client()
    resp = client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "admin123"}
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    resp = client.post(
        "/api/deliverables",
        json={"kind": "email", "title": "Route email %s" % TOKEN, "content": "<html>x</html>"},
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    item = resp.get_json()["deliverable"]

    resp = client.post("/api/deliverables/%d/publish" % item["id"], json={})
    assert resp.status_code == 400, resp.get_data(as_text=True)
    assert "website" in resp.get_json()["error"], resp.get_json()

    anon = appmod.app.test_client()
    assert anon.post("/api/deliverables/%d/publish" % item["id"], json={}).status_code != 200
    print("publish route:", resp.get_json()["error"])


def test_a_second_save_updates_the_same_draft():
    """The team saves before the images are chosen, then again once they are."""
    org = _org("Deliverable Save Twice Client")
    job_id = _job_in_org(org["id"])
    tool = _save_tool(job_id)
    asyncio.run(tool.run_json(
        {"kind": "email", "title": "Cows", "content": "<p>no images yet</p>"}, DRY))
    second = asyncio.run(tool.run_json(
        {"kind": "email", "title": "Cows",
         "content": '<p><img src="https://images.example/cow.jpg" alt="cow"></p>'}, DRY))
    rows = deliverables.list_deliverables("email", org["id"])
    assert len(rows) == 1, rows
    assert "img src" in rows[0]["content"], rows[0]["content"]
    assert second.startswith("Updated"), second
    print("second save updated draft #%s instead of duplicating it" % rows[0]["id"])


def test_a_save_without_content_is_explained():
    org = _org("Deliverable No Content Client")
    reply = asyncio.run(_save_tool(_job_in_org(org["id"])).run_json(
        {"kind": "email", "title": "Empty"}, DRY))
    assert "content is required" in reply, reply
    assert deliverables.list_deliverables("email", org["id"]) == []
    print("missing content refused with a message the model can act on")


def main():
    test_tool_saves_a_draft_in_the_jobs_org()
    test_the_tool_refuses_an_unknown_kind()
    test_content_rule_travels_with_the_tool()
    test_page_paths()
    test_publish_needs_a_site()
    test_publish_is_a_dry_run_and_leaves_the_page_a_draft()
    test_a_live_page_cannot_be_overwritten_by_slug()
    test_only_website_pages_can_be_published()
    test_api_offers_the_website_kind()
    test_publish_route_validates_the_kind_and_the_session()
    test_a_second_save_updates_the_same_draft()
    test_a_save_without_content_is_explained()
    print("\nDELIVERABLES OK")


if __name__ == "__main__":
    main()
