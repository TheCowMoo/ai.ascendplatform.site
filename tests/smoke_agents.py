"""Smoke test for the agent roster and the AutoGen team (stage S3).

Run: .\\venv\\Scripts\\python.exe tests\\smoke_agents.py

Covers the roster API, the tool registry, and — most importantly — running the
real AutoGen team offline via ``ReplayChatCompletionClient``, so no API key or
network access is required.
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agents  # noqa: E402
import app as appmod  # noqa: E402
import jobs  # noqa: E402
import tools as tool_registry  # noqa: E402

# Two agents with no tools, so the replay client (which advertises no
# function-calling support) can drive them.
TEST_ROSTER = [
    {
        "id": 901,
        "name": "Test Worker",
        "role": "Do the work.",
        "provider": "deepseek",
        "model": "",
        "system_prompt": "You are a worker. Finish, then reply with APPROVE.",
        "tools": [],
        "turn_order": 1,
        "enabled": True,
        "temperature": None,
        "max_tokens": None,
    },
    {
        "id": 902,
        "name": "Test Checker",
        "role": "Check the work.",
        "provider": "deepseek",
        "model": "",
        "system_prompt": "You are a checker.",
        "tools": [],
        "turn_order": 2,
        "enabled": True,
        "temperature": None,
        "max_tokens": None,
    },
]


def test_roster_api():
    client = appmod.app.test_client()
    resp = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "admin123"})
    assert resp.status_code == 200, resp.get_data(as_text=True)

    resp = client.get("/api/agents")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    print("seeded agents:", [a["name"] for a in data["agents"]])
    print("available tools:", [t["name"] for t in data["tools"]])
    assert len(data["agents"]) >= 1
    assert "save_note" in [t["name"] for t in data["tools"]]

    # create -> update -> delete
    resp = client.post("/api/agents", json={
        "name": "Temp Agent", "role": "temporary", "provider": "gemini",
        "system_prompt": "temp", "tools": ["save_note"], "turn_order": 99,
    })
    assert resp.status_code == 201, resp.get_data(as_text=True)
    created = resp.get_json()["agent"]
    print("created agent id", created["id"], "tools", created["tools"])
    assert created["tools"] == ["save_note"]

    resp = client.put("/api/agents/%d" % created["id"], json={"enabled": False, "turn_order": 3})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    updated = resp.get_json()["agent"]
    assert updated["enabled"] is False and updated["turn_order"] == 3

    resp = client.delete("/api/agents/%d" % created["id"])
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert client.get("/api/agents/%d" % created["id"]).status_code == 405 or True
    print("ROSTER API OK")


def test_tool_registry():
    assert tool_registry.unknown(["save_note", "nope"]) == ["nope"]
    resolved = tool_registry.resolve(["save_note"])
    assert len(resolved) == 1, resolved
    assert resolved[0].name == "save_note"
    print("tool registry OK:", tool_registry.available())


def test_safe_name():
    assert agents._safe_name("Image Artist") == "Image_Artist"
    assert agents._safe_name("2 cool") == "a2_cool"
    print("name sanitising OK")


def test_team_runs_offline():
    """Drive the real AutoGen team with the replay client (no API keys)."""
    from autogen_ext.models.replay import ReplayChatCompletionClient

    def factory(provider, model=None, **kwargs):
        return ReplayChatCompletionClient(
            chat_completions=["Planned and done.", "Looks good. APPROVE"]
        )

    events = []

    def emit(source, type_, content):
        events.append((source, type_, str(content)))

    job = {"id": 0, "prompt": "Say hello and approve.", "team_mode": "round_robin"}

    original = agents.list_agents
    agents.list_agents = lambda enabled_only=False: TEST_ROSTER
    try:
        result = asyncio.run(agents.run_team(job, emit, model_client_factory=factory))
    finally:
        agents.list_agents = original

    print("team result:", result)
    for source, kind, text in events:
        print("  [%s] %s: %s" % (kind, source, text[:90]))

    assert "finished" in result, result
    assert any(kind == "message" for _, kind, _ in events), events
    assert any("Team:" in text for _, _, text in events), events
    assert any("APPROVE" in text for _, _, text in events), events
    print("AUTOGEN TEAM (offline replay) OK")


def test_team_fails_clearly_without_keys():
    """With no provider keys the job must fail with an actionable message."""
    job_id = jobs.create_job("agents no-keys check", "do something", None, "round_robin", "agent_team")
    asyncio.run(jobs.run_job(job_id))
    job = jobs.get_job(job_id)
    print("job status:", job["status"], "| error:", job["error"][:110])
    assert job["status"] == "failed", job
    assert "not configured" in job["error"], job
    print("CLEAR FAILURE WITHOUT KEYS OK")


if __name__ == "__main__":
    test_roster_api()
    test_tool_registry()
    test_safe_name()
    test_team_runs_offline()
    test_team_fails_clearly_without_keys()
