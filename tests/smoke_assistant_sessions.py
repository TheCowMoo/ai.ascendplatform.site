"""The Assistant's new/old chats: private, server-backed and continuable.

Run: .\\venv\\Scripts\\python.exe tests\\smoke_assistant_sessions.py

The session is per user and acting client. A client admin in the same org must not see
the owner's session; another client must not see it either. The manager route builds
history from the stored session, not from whatever the browser sends.
"""

import os
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.pop("ALLOW_LIVE_ACTIONS", None)

import app as appmod  # noqa: E402
import assistant_sessions as sessions  # noqa: E402
import db  # noqa: E402
import jobs  # noqa: E402
import llm  # noqa: E402
import orgs as orgs_service  # noqa: E402

FAILED = []
TOKEN = uuid.uuid4().hex[:6]


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name +
          ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


def _org(name):
    return orgs_service.create_org({"name": "%s %s" % (name, TOKEN)})


def _login(email="admin@example.com", password="admin123"):
    client = appmod.app.test_client()
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return client


def _pick(client, org_id):
    resp = client.post("/api/session/org", json={"org_id": org_id})
    assert resp.status_code == 200, resp.get_data(as_text=True)


def test_service_roundtrip():
    org = _org("Assistant Session")
    other = _org("Assistant Other")
    session = sessions.create_session(1, org["id"], "Planning")
    sessions.add_message(session["id"], "user", "Build a quiz")
    sessions.add_message(session["id"], "assistant", "Which audience?")
    check("a session keeps its title", sessions.get_session(
        session["id"], 1, org["id"])["title"], "Planning")
    listed = sessions.list_sessions(1, org["id"])
    check("the session is in the list",
          any(row["id"] == session["id"] for row in listed), True)
    data = sessions.get_session_with_messages(session["id"], 1, org["id"])
    check("the transcript is chronological",
          [m["content"] for m in data["messages"]],
          ["Build a quiz", "Which audience?"])
    check("another client cannot read it",
          sessions.get_session(session["id"], 1, other["id"]), None)
    check("another user cannot read it",
          sessions.get_session(session["id"], 2, org["id"]), None)

    # The manager records the answer by job id; a retry must not duplicate it.
    job_id = 900000 + (int(TOKEN, 16) % 100000)
    sessions.add_message(session["id"], "user", "Second turn", job_id=job_id)
    first = sessions.record_reply_for_job(job_id, "The second answer")
    again = sessions.record_reply_for_job(job_id, "The second answer")
    check("the reply is stored", first["role"], "assistant")
    check("a retry does not duplicate the reply", again["id"], first["id"])
    check("the turn count is four",
          len(sessions.get_session_with_messages(
              session["id"], 1, org["id"])["messages"]), 4)

def test_api_and_manager():
    org = _org("Assistant API")
    other = _org("Assistant API Other")
    client = _login()
    _pick(client, org["id"])

    session = sessions.create_session(1, org["id"], "API chat")
    sessions.add_message(session["id"], "user", "Hello API")
    sessions.add_message(session["id"], "assistant", "Hi")
    listed = client.get("/api/assistant/sessions").get_json()["sessions"]
    check("the API lists the session",
          any(row["id"] == session["id"] for row in listed), True)
    got = client.get("/api/assistant/sessions/%d" % session["id"]).get_json()
    check("the API returns the messages", len(got["messages"]), 2)

    _pick(client, other["id"])
    check("another client's list is empty",
          client.get("/api/assistant/sessions").get_json()["sessions"], [])
    check("another client cannot read the session",
          client.get("/api/assistant/sessions/%d" % session["id"]).status_code, 404)
    _pick(client, org["id"])

    email = "assistant-other-%s@example.test" % TOKEN
    orgs_service.add_org_user(org["id"], {
        "email": email, "password": "secret123",
        "name": "Other Admin", "role": "client_admin"})
    member = _login(email, "secret123")
    check("a user in the same client cannot see the owner's chats",
          member.get("/api/assistant/sessions").get_json()["sessions"], [])
    check("nor read one by id",
          member.get("/api/assistant/sessions/%d" % session["id"]).status_code, 404)

    made = client.post("/api/assistant/sessions", json={})
    check("an empty session can be created for voice", made.status_code, 201)
    voice_id = made.get_json()["session"]["id"]
    turn = client.post("/api/assistant/sessions/%d/messages" % voice_id,
                       json={"role": "user", "content": "Voice turn"})
    check("a voice turn is stored", turn.status_code, 201)
    check("the voice turn is in the transcript",
          sessions.get_session_with_messages(voice_id, 1, org["id"])["messages"][0]["content"],
          "Voice turn")
    bad = client.post("/api/assistant/sessions/%d/messages" % voice_id,
                      json={"role": "system", "content": "x"})
    check("only user/assistant roles are accepted", bad.status_code, 400)
    empty = client.post("/api/assistant/sessions/%d/messages" % voice_id,
                        json={"role": "user", "content": "  "})
    check("empty voice turns are refused", empty.status_code, 400)

    imported = client.post("/api/assistant/sessions/import", json={"messages": [
        {"role": "user", "content": "Imported question"},
        {"role": "assistant", "content": "Imported answer"},
    ]})
    check("the import endpoint accepts an old thread", imported.status_code, 201)
    check("the import reports its messages",
          imported.get_json().get("imported"), 2)

    # The manager route must build history from the session, not the browser.
    original_create, original_submit = jobs.create_job, jobs.submit
    original_providers = llm.configured_providers
    captured = {}

    def fake_create(title, prompt, requester=None, team_mode="selector",
                    task_name="echo", org_id=None):
        captured["prompt"] = prompt
        captured["org"] = org_id
        return 424242

    jobs.create_job = fake_create
    jobs.submit = lambda job_id: captured.setdefault("submitted", job_id)
    llm.configured_providers = lambda: ["deepseek"]
    try:
        resp = client.post("/api/manager/chat", json={
            "message": "Continue here", "session_id": session["id"]})
        check("a session turn is accepted", resp.status_code, 201)
        check("the same session is returned",
              resp.get_json().get("session_id"), session["id"])
        check("the prompt includes stored history",
              "Hello API" in captured["prompt"], True)
        check("the prompt includes the new message",
              "Continue here" in captured["prompt"], True)
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT job_id FROM assistant_messages WHERE session_id = ? "
                "AND content = 'Continue here'", (session["id"],)).fetchone()
        check("the user turn is linked to its job", row["job_id"], 424242)
        check("the job was submitted", captured.get("submitted"), 424242)

        fresh = client.post("/api/manager/chat", json={
            "message": "Fresh start", "create_session": True})
        check("a new chat creates a session", fresh.status_code, 201)
        fresh_id = fresh.get_json().get("session_id")
        check("the new session id is returned", bool(fresh_id), True)
        fresh_data = sessions.get_session_with_messages(fresh_id, 1, org["id"])
        check("the first user turn is stored",
              fresh_data["messages"][0]["content"], "Fresh start")
        check("the first turn titles the chat",
              fresh_data["session"]["title"], "Fresh start")
    finally:
        jobs.create_job, jobs.submit = original_create, original_submit
        llm.configured_providers = original_providers


if __name__ == "__main__":
    test_service_roundtrip()
    test_api_and_manager()
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nASSISTANT SESSIONS OK - new chats, old chats, and server-built history")

