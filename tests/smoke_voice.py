"""The spoken assistant: Gemini is the mouth, our agents are the hands.

What is tested is the boundary, not the audio: a token minted server-side, a status that says
*why* the microphone is missing, and a delegation route that can only ever start a normal
manager job. Audio never enters these tests because it never enters this server.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.pop("ALLOW_LIVE_ACTIONS", None)

import app as appmod  # noqa: E402
import db  # noqa: E402
import voice  # noqa: E402

FAILED = []


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


def test_status_tells_the_truth():
    original = voice.api_key
    try:
        voice.api_key = lambda: ""
        state = voice.status(1)
        check("with no key the microphone is unavailable", state["available"], False)
        check("and it says where to fix it", "Settings" in state["reason"], True)
        check("a key-less session cannot be offered minutes", state["remaining_minutes"], 0)

        voice.api_key = lambda: "test-key"
        state = voice.status(1)
        check("with a key it is available", state["available"], True)
        check("and reports the voice it will use", bool(state["voice"]), True)
        check("with a session cap", state["session_seconds"], voice.SESSION_SECONDS)

        voice.record_use(99, voice.daily_minutes() * 60)
        spent = voice.status(99)
        check("a spent allowance is refused", spent["available"], False)
        check("and says minutes, not a key", "minutes" in spent["reason"], True)
    finally:
        voice.api_key = original


def test_setup_declares_one_tool():
    setup = voice.setup()["setup"]
    names = [item["name"] for item in setup["tools"][0]["functionDeclarations"]]
    check("the voice has exactly one tool", names, ["delegate_to_team"])
    check("it asks for audio back", setup["generationConfig"]["responseModalities"], ["AUDIO"])
    text = setup["systemInstruction"]["parts"][0]["text"]
    check("the instruction forbids claiming work it did not do",
          "Never claim work is done" in text, True)
    check("and forbids inventing credentials", "Never invent" in text, True)


def test_the_routes():
    client = appmod.app.test_client()
    check("status needs a login", client.get("/api/voice/status").status_code, 401)
    client.post("/api/auth/login", json={"email": "admin@example.com", "password": "admin123"})

    original_key, original_mint = voice.api_key, voice.mint
    try:
        voice.api_key = lambda: ""
        refused = client.post("/api/voice/session", json={"page": "forms"})
        check("a session without a key is refused", refused.status_code, 400)
        check("with a fallback flag for the UI",
              (refused.get_json() or {}).get("fallback"), "text")

        voice.api_key = lambda: "test-key"
        voice.mint = lambda: "ephemeral-token"
        session = client.post("/api/voice/session", json={"page": "forms"})
        data = session.get_json() or {}
        check("a session mints a token", data.get("token"), "ephemeral-token")
        check("the socket endpoint is the constrained one",
              "BidiGenerateContentConstrained" in (data.get("endpoint") or ""), True)
        check("the setup carries the tool",
              data["setup"]["setup"]["tools"][0]["functionDeclarations"][0]["name"],
              "delegate_to_team")

        def boom():
            raise voice.VoiceError("Gemini refused to mint a session token (403): denied")

        voice.mint = boom
        failed = client.post("/api/voice/session", json={})
        check("a mint failure is a 502, not a crash", failed.status_code, 502)
        check("and the message is passed through",
              "refused" in (failed.get_json() or {}).get("error", ""), True)
    finally:
        voice.api_key, voice.mint = original_key, original_mint

    bad = client.post("/api/voice/tool", json={"name": "create_form", "args": {}})
    check("only delegate_to_team may be called", bad.status_code, 403)
    empty = client.post("/api/voice/tool", json={"name": "delegate_to_team", "args": {}})
    check("an empty instruction is refused", empty.status_code, 400)

    orgs = client.get("/api/orgs").get_json().get("orgs") or []
    if orgs:
        client.post("/api/session/org", json={"org_id": orgs[0]["id"]})
    with db.get_connection() as conn:
        before = conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE title = 'Voice request'"
                              ).fetchone()["n"]
    made = client.post("/api/voice/tool",
                       json={"name": "delegate_to_team", "turn_id": "turn-1",
                             "args": {"instruction": "build a trip quiz"}})
    check("a delegation is accepted", made.status_code, 202)
    job_id = (made.get_json() or {}).get("job_id")
    check("and starts a normal manager job", bool(job_id), True)
    with db.get_connection() as conn:
        after = conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE title = 'Voice request'"
                             ).fetchone()["n"]
        rows = conn.execute("SELECT task, org_id FROM jobs WHERE id = ?", (job_id,)).fetchall()
        conn.execute("DELETE FROM job_events WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    check("one job was created", after - before, 1)
    check("it is a chat job, the same as typing", [row["task"] for row in rows], ["chat"])

    again = client.post("/api/voice/tool",
                        json={"name": "delegate_to_team", "turn_id": "turn-1",
                              "args": {"instruction": "build a trip quiz"}})
    check("the same turn is not run twice", (again.get_json() or {}).get("duplicate"), True)


if __name__ == "__main__":
    test_status_tells_the_truth()
    test_setup_declares_one_tool()
    test_the_routes()
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nVOICE OK - the key stays here, the token expires, and the voice can only delegate")
