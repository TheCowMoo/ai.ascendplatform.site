"""The typed chat and the spoken chat are one thread.

Run: .\\venv\\Scripts\\python.exe tests\\test_chat_thread.py

The sheet used to send its own last-8 array and the voice sent no session at all, so the
Manager started every spoken turn from nothing. These assertions pin the server side:
the second typed turn carries the first, a voice job carries the session transcript, a
welcome with no session starts one at its first real request, and another client's
session is never usable.
"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="chat-thread-"), "test.db").replace("\\", "/")
os.environ.pop("ALLOW_LIVE_ACTIONS", None)
os.environ.pop("JOB_BACKEND", None)
os.environ.pop("REDIS_URL", None)

import app as appmod  # noqa: E402
import assistant_sessions as sessions  # noqa: E402
import db  # noqa: E402
import jobs  # noqa: E402
import llm  # noqa: E402
import orgs as orgs_service  # noqa: E402

db.init_db()
FAILED = []
TOKEN = str(os.getpid())


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


class quiet:
    """Keep jobs queued so the test reads their prompts instead of running a model."""

    def __enter__(self):
        self.providers = llm.configured_providers
        self.submit = jobs.submit
        llm.configured_providers = lambda: ["deepseek"]
        jobs.submit = lambda job_id: None

    def __exit__(self, *exc):
        llm.configured_providers = self.providers
        jobs.submit = self.submit


def org(name):
    return orgs_service.create_org({"name": "%s %s" % (name, TOKEN)})


def login():
    client = appmod.app.test_client()
    resp = client.post("/api/auth/login", json={"email": "admin@example.com",
                                                "password": "admin123"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return client


def pick(client, org_id):
    resp = client.post("/api/session/org", json={"org_id": org_id})
    assert resp.status_code == 200, resp.get_data(as_text=True)


def test_the_typed_thread_and_context():
    client = login()
    target = org("Thread Typed")
    pick(client, target["id"])
    session = sessions.create_session(1, target["id"], "")
    with quiet():
        first = client.post("/api/manager/chat", json={
            "message": "Build a page about cows", "context": "studio screen",
            "session_id": session["id"]})
        check("the first typed turn is accepted", first.status_code, 201)
        first_id = first.get_json()["job_id"]
        jobs.add_event(first_id, "Manager", "message", "The page is ready.")
        sessions.record_reply_for_job(first_id, "The page is ready.")
        second = client.post("/api/manager/chat", json={
            "message": "Make the headline shorter", "context": "pages screen",
            "session_id": session["id"]})
        check("the second typed turn is accepted", second.status_code, 201)
        second_job = jobs.get_job(second.get_json()["job_id"])
    prompt = (second_job or {}).get("prompt") or ""
    check("the second prompt carries the earlier user turn",
          "Build a page about cows" in prompt, True)
    check("the second prompt carries the earlier answer",
          "The page is ready." in prompt, True)
    check("the screen context is in the prompt",
          "[context: the user is on the pages screen]" in prompt, True)
    stored = sessions.get_session_with_messages(session["id"], 1, target["id"])
    check("the stored turn is only the user's words",
          stored["messages"][-1]["content"], "Make the headline shorter")
    check("and the chrome is not the chat title",
          stored["session"]["title"], "Build a page about cows")


def test_a_voice_job_carries_the_same_thread():
    client = login()
    target = org("Thread Voice")
    pick(client, target["id"])
    session = sessions.create_session(1, target["id"], "")
    sessions.add_message(session["id"], "user", "Build a trip quiz")
    sessions.add_message(session["id"], "assistant", "Who is it for?")
    with quiet():
        made = client.post("/api/voice/tool", json={
            "name": "delegate_to_team", "turn_id": "thread-turn-1",
            "session_id": session["id"],
            "args": {"instruction": "Make it warmer"}})
        check("the voice delegation is accepted", made.status_code, 202)
        data = made.get_json() or {}
        job = jobs.get_job(data.get("job_id")) or {}
    check("the voice response names the session", data.get("session_id"), session["id"])
    prompt = job.get("prompt") or ""
    check("the voice Manager sees the prior user turn",
          "Build a trip quiz" in prompt, True)
    check("the voice Manager sees the prior answer", "Who is it for?" in prompt, True)
    check("and the current instruction", "Make it warmer" in prompt, True)


def test_the_welcome_starts_at_its_first_real_request():
    client = login()
    target = org("Thread Welcome")
    pick(client, target["id"])
    with quiet():
        made = client.post("/api/voice/tool", json={
            "name": "delegate_to_team", "turn_id": "welcome-turn-1",
            "args": {"instruction": "Build me a site"}})
        check("the welcome request is accepted", made.status_code, 202)
        data = made.get_json() or {}
        job_id = data.get("job_id")
        created = sessions.get_session_with_messages(data.get("session_id"), 1, target["id"])
    check("the request starts a session", bool(data.get("session_id")), True)
    check("the first stored turn is the request",
          created["messages"][0]["content"], "Build me a site")
    check("and it is linked to the job", created["messages"][0]["job_id"], job_id)
    sessions.record_reply_for_job(job_id, "The site is ready.")
    after = sessions.get_session_with_messages(data["session_id"], 1, target["id"])
    check("the Manager answer is stored in the same thread",
          after["messages"][-1]["content"], "The site is ready.")
    check("the welcome itself never entered the thread",
          any("welcome" in (message.get("content") or "").lower()
              for message in after["messages"]), False)


def test_another_clients_session_is_refused():
    mine = org("Thread Mine")
    theirs = org("Thread Theirs")
    foreign = sessions.create_session(1, theirs["id"], "Their chat")
    sessions.add_message(foreign["id"], "user", "Their private request")
    client = login()
    pick(client, mine["id"])
    with quiet():
        refused = client.post("/api/voice/tool", json={
            "name": "delegate_to_team", "turn_id": "foreign-turn",
            "session_id": foreign["id"], "args": {"instruction": "read that chat"}})
        check("a foreign session is not usable", refused.status_code, 404)
        missing = client.post("/api/voice/tool", json={
            "name": "delegate_to_team", "turn_id": "missing-turn",
            "session_id": 999999, "args": {"instruction": "read that chat"}})
        check("a stale session is a 404 for the client to retry", missing.status_code, 404)


if __name__ == "__main__":
    try:
        test_the_typed_thread_and_context()
        test_a_voice_job_carries_the_same_thread()
        test_the_welcome_starts_at_its_first_real_request()
        test_another_clients_session_is_refused()
    finally:
        if FAILED:
            print("\n%d FAILED" % len(FAILED))
            raise SystemExit(1)
        print("\nCHAT THREAD OK - typing and speaking share one session, and New is explicit")
