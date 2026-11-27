"""Recommendations: evidence, cache, tenant safety and the AI merge boundary.

Run: .\\venv\\Scripts\\python.exe tests\\test_recommendations.py

The AI provider is never called. The checks pin the part that must work without one:
facts become evidence-backed cards, one batch is cached per client, a change
invalidates it, and an AI answer cannot introduce an unknown key or drop the
server-side evidence.
"""

import asyncio
import json
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="recommendations-"), "test.db").replace("\\", "/")
os.environ["SCHEDULER_ENABLED"] = "0"
os.environ["PLATFORM_URL"] = "https://platform.test"
os.environ.pop("ALLOW_LIVE_ACTIONS", None)
os.environ.pop("JOB_BACKEND", None)
os.environ.pop("REDIS_URL", None)

import app as appmod  # noqa: E402,F401
import db  # noqa: E402
import forms as forms_service  # noqa: E402
import jobs  # noqa: E402
import orgs as orgs_service  # noqa: E402
import recommendations  # noqa: E402
import sites as sites_service  # noqa: E402
import tools as registry  # noqa: E402
import tools.recommendations as recommendation_tools  # noqa: E402

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


def new_job(org_id):
    return jobs.create_job("Recommendation test", "test", admin_id(),
                           "selector", "chat", org_id=org_id)


def keys(data):
    return [item["key"] for item in data.get("recommendations") or []]


def without_ai(fn):
    """Suppress the optional job queue while asserting deterministic behaviour."""
    original = recommendations._ai_available
    recommendations._ai_available = lambda: False
    try:
        return fn()
    finally:
        recommendations._ai_available = original


def test_onboarding_recommendations_are_explained_and_cached():
    org_id = new_org("Recommendation Client")
    first = without_ai(lambda: recommendations.get(org_id, queue_ai=False))
    check("an empty client gets onboarding ideas", "complete_profile" in keys(first), True)
    check("and a path to a site", "get_site_live" in keys(first), True)
    check("and an enquiry form", "add_form" in keys(first), True)
    check("every idea has a reason",
          all(item.get("why") for item in first["recommendations"]), True)
    check("every idea has evidence",
          all(item.get("evidence") for item in first["recommendations"]), True)
    second = without_ai(lambda: recommendations.get(org_id, queue_ai=False))
    check("the batch is cached", second["generated_at"], first["generated_at"])

    site = sites_service.create_site({"domain": "rec-check.test"}, org_id=org_id)
    changed = without_ai(lambda: recommendations.get(org_id, queue_ai=False))
    check("a new site invalidates the cache", "finish_site" in keys(changed), True)
    check("the new site is reflected", "finish_site" in keys(changed), True)
    site_card = [item for item in changed["recommendations"]
                 if item["key"] == "finish_site"][0]
    check("the recommendation is evidence-backed",
          any("Site status" in item for item in site_card["evidence"]), True)
    check("a site id is not exposed in the action text",
          str(site["id"]) in site_card["action_text"], False)
def test_forms_and_leads_produce_actions():
    org_id = new_org("Recommendation Leads")
    form = forms_service.create_form({
        "name": "Enquiry", "kind": "form",
        "questions": [
            {"id": "name", "label": "Your name", "type": "text",
             "page": 0, "required": True},
            {"id": "email", "label": "Your email", "type": "email",
             "page": 0, "required": True},
        ],
    }, org_id)
    form = forms_service.update_form(form["id"], org_id, {"status": "published"})
    forms_service.submit(form, {"name": "Ada", "email": "ada@example.test"})
    data = without_ai(lambda: recommendations.get(org_id, queue_ai=False))
    check("an answered form gets a follow-up or share action",
          "follow_up_leads" in keys(data) or "share_form" in keys(data), True)
    check("the action is about the form or enquiry",
          any("form" in item["action_text"].lower()
              or "enquir" in item["action_text"].lower()
              for item in data["recommendations"]), True)
    check("no lead email is copied into the recommendation",
          "ada@example.test" in json.dumps(data), False)


def test_dismissing_hides_a_card_and_is_tenant_scoped():
    org_id = new_org("Recommendation Dismiss")
    other = new_org("Recommendation Other")
    data = without_ai(lambda: recommendations.get(org_id, queue_ai=False))
    key = data["recommendations"][0]["key"]
    changed = recommendations.set_status(org_id, key, "dismissed")
    check("dismissing returns a batch", bool(changed), True)
    check("the dismissed card is hidden",
          key in keys(without_ai(lambda: recommendations.get(org_id, queue_ai=False))),
          False)
    check("another client still has it",
          key in keys(without_ai(lambda: recommendations.get(other, queue_ai=False))),
          True)


def test_ai_merge_only_keeps_real_keys_and_server_evidence():
    cards = [
        {"key": "get_site_live", "title": "Old", "why": "Old why",
         "action_text": "Old action", "evidence": ["No connected sites"],
         "audience": "all", "priority": 2, "status": "open"},
    ]
    text = json.dumps({"recommendations": [
        {"key": "get_site_live", "title": "Start with a domain",
         "why": "There is no site yet.",
         "action_text": "Help me pick and connect a domain."},
        {"key": "invented", "title": "Not real", "why": "nope",
         "action_text": "Do not run this."},
    ]})
    merged = recommendations._parse_ai(text, cards)
    check("only the real key survives", [item["key"] for item in merged],
          ["get_site_live"])
    check("AI can reword the title", merged[0]["title"], "Start with a domain")
    check("the server keeps the evidence", merged[0]["evidence"],
          ["No connected sites"])
    check("free-form invalid output is ignored",
          recommendations._parse_ai("not json", cards), [])
    invented = json.dumps({"recommendations": [
        {"key": "get_site_live", "title": "Traffic dropped 42%",
         "why": "42% fewer visitors.", "action_text": "Fix it."},
    ]})
    check("an invented metric is rejected",
          recommendations._parse_ai(invented, cards), [])


def test_refine_uses_only_validated_ai_wording():
    import autogen_agentchat.agents as agents_module
    import llm

    org_id = new_org("Recommendation AI")
    data = without_ai(lambda: recommendations.get(org_id, queue_ai=False))
    key = data["recommendations"][0]["key"]
    old_title = data["recommendations"][0]["title"]
    original_agent = agents_module.AssistantAgent
    original_providers = llm.configured_providers
    original_client = llm.get_model_client

    class FakeAgent:
        def __init__(self, **kwargs):
            pass

        async def run(self, task):
            payload = {"recommendations": [
                {"key": key, "title": "AI-ranked first step",
                 "why": "The evidence is still on the card.",
                 "action_text": "Do the first step."},
                {"key": "invented-by-the-model", "title": "No evidence",
                 "why": "nope", "action_text": "nope"},
            ]}
            return types.SimpleNamespace(
                messages=[types.SimpleNamespace(content=json.dumps(payload))])

    agents_module.AssistantAgent = FakeAgent
    llm.configured_providers = lambda: ["fake"]
    llm.get_model_client = lambda provider, **kwargs: object()
    try:
        result = asyncio.run(recommendations.refine(org_id))
        check("refine reports success", "Refreshed" in result, True)
    finally:
        agents_module.AssistantAgent = original_agent
        llm.configured_providers = original_providers
        llm.get_model_client = original_client
    after = without_ai(lambda: recommendations.get(org_id, queue_ai=False))
    check("the AI wording is cached", after["source"], "ai")
    check("the AI title is shown", after["recommendations"][0]["title"],
          "AI-ranked first step")
    check("the deterministic evidence remains",
          bool(after["recommendations"][0]["evidence"]), True)
    check("the invented key is not shown",
          "invented-by-the-model" in [item["key"] for item in after["recommendations"]],
          False)
    check("the old title was replaced", after["recommendations"][0]["title"] != old_title,
          True)


def test_ai_refresh_is_rate_limited_and_coalesced():
    org_id = new_org("Recommendation Refresh Limit")
    without_ai(lambda: recommendations.get(org_id, queue_ai=False))
    with db.get_connection() as conn:
        conn.execute("UPDATE recommendation_batches SET ai_at = ? WHERE org_id = ?",
                     (db.now_str(), org_id))
    original_available = recommendations._ai_available
    original_job_open = recommendations._ai_job_open
    original_create = jobs.create_job
    original_submit = jobs.submit
    created = []
    recommendations._ai_available = lambda: True
    recommendations._ai_job_open = lambda org: False
    jobs.create_job = lambda *args, **kwargs: created.append(1) or 1
    jobs.submit = lambda job_id: True
    try:
        check("a second refresh inside the window does not call AI",
              recommendations._queue_ai(org_id, force=True), False)
        with db.get_connection() as conn:
            conn.execute("UPDATE recommendation_batches SET ai_at = '' WHERE org_id = ?",
                         (org_id,))
        check("after the window the job is queued",
              recommendations._queue_ai(org_id, force=True), True)
        check("exactly one AI job was created", len(created), 1)
    finally:
        recommendations._ai_available = original_available
        recommendations._ai_job_open = original_job_open
        jobs.create_job = original_create
        jobs.submit = original_submit


def test_members_do_not_get_admin_only_cards():
    row = {
        "payload": json.dumps([
            {"key": "public", "title": "General", "status": "open",
             "audience": "all", "priority": 1, "evidence": ["x"]},
            {"key": "private", "title": "Analytics detail", "status": "open",
             "audience": "admin", "priority": 2, "evidence": ["y"]},
        ]),
        "generated_at": "", "source": "rules", "org_id": 1,
    }
    original = recommendations._ai_job_open
    recommendations._ai_job_open = lambda org_id: False
    try:
        member_view = recommendations._public(row, include_admin=False)
        admin_view = recommendations._public(row, include_admin=True)
    finally:
        recommendations._ai_job_open = original
    check("members see the general card",
          [item["key"] for item in member_view["recommendations"]], ["public"])
    check("admins see both",
          [item["key"] for item in admin_view["recommendations"]],
          ["public", "private"])


def test_the_manager_tool_and_api_are_wired():
    org_id = new_org("Recommendation API")
    job = new_job(org_id)
    tool = recommendation_tools._build_recommend_next_steps(None, job_id=job)
    listing = json.loads(asyncio.run(tool()))
    check("the manager tool returns a JSON list", bool(listing), True)
    check("the tool is registered", "recommend_next_steps" in registry.available(), True)
    check("the tool is read-only", "recommend_next_steps" in registry.READ_ONLY_TOOLS, True)

    client = appmod.app.test_client()
    response = client.post("/api/auth/login", json={
        "email": "admin@example.com", "password": "admin123"})
    check("the test admin can log in", response.status_code, 200)
    response = client.post("/api/session/org", json={"org_id": org_id})
    check("the test admin can pick the client", response.status_code, 200)
    original = recommendations._ai_available
    recommendations._ai_available = lambda: False
    try:
        response = client.get("/api/recommendations")
        check("the API answers", response.status_code, 200)
        check("the API includes a card",
              bool((response.get_json() or {}).get("recommendations")), True)
        refreshed = client.post("/api/recommendations/refresh")
        check("refresh answers", refreshed.status_code, 200)
        key = (refreshed.get_json() or {})["recommendations"][0]["key"]
        dismissed = client.post("/api/recommendations/%s/dismiss" % key)
        check("dismiss answers", dismissed.status_code, 200)
        check("the card is gone",
              key in [item["key"] for item in
                      (dismissed.get_json() or {}).get("recommendations", [])], False)
    finally:
        recommendations._ai_available = original


if __name__ == "__main__":
    test_onboarding_recommendations_are_explained_and_cached()
    test_forms_and_leads_produce_actions()
    test_dismissing_hides_a_card_and_is_tenant_scoped()
    test_ai_merge_only_keeps_real_keys_and_server_evidence()
    test_refine_uses_only_validated_ai_wording()
    test_ai_refresh_is_rate_limited_and_coalesced()
    test_members_do_not_get_admin_only_cards()
    test_the_manager_tool_and_api_are_wired()
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nRECOMMENDATIONS OK - evidence, cache, tenant and merge rules")

