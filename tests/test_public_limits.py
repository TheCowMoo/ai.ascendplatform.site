"""Public surfaces are bounded before they reach the model or the CRM.

Run: .\\venv\\Scripts\\python.exe tests\\test_public_limits.py

The widget is unauthenticated and spends the agency's model credits. A turn is
length-checked, history is validated, one caller cannot loop it, and an oversized
body is refused before Flask buffers it.
"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="public-limits-"), "test.db").replace("\\", "/")
os.environ["SCHEDULER_ENABLED"] = "0"
os.environ.pop("ALLOW_LIVE_ACTIONS", None)

import app as appmod  # noqa: E402
import db  # noqa: E402
import orgs as orgs_service  # noqa: E402

db.init_db()

FAILED = []
BOT = "limitbot"


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


CLIENT = None


def make_bot():
    global CLIENT
    if CLIENT is not None:
        return CLIENT
    org = orgs_service.create_org({"name": "Public Limits"})["id"]
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO bots (id, name, assistant_name, org_id) VALUES (?, ?, ?, ?)",
            (BOT, "Limit bot", "Limit", org))
    CLIENT = appmod.app.test_client()
    return CLIENT


def test_a_caller_cannot_loop_the_widget():
    client = make_bot()
    appmod._RATE_HITS.clear()
    headers = {"X-Real-IP": "198.51.100.61"}
    codes = []
    for _ in range(30):
        codes.append(client.post("/api/chat", json={"bot_id": BOT, "message": "hi"},
                                 headers=headers).status_code)
    check("thirty turns are served", set(codes), {200})
    resp = client.post("/api/chat", json={"bot_id": BOT, "message": "hi"},
                       headers=headers)
    check("the next one is throttled", resp.status_code, 429)


def test_a_turn_and_its_history_are_length_checked():
    client = make_bot()
    appmod._RATE_HITS.clear()
    resp = client.post("/api/chat",
                       json={"bot_id": BOT, "message": "x" * (appmod.MAX_CHAT_MESSAGE + 1)},
                       headers={"X-Real-IP": "198.51.100.62"})
    check("an oversized message is refused", resp.status_code, 400)

    resp = client.post("/api/chat", json={
        "bot_id": BOT, "message": "hi",
        "history": [{"role": "user", "content": "x" * (appmod.MAX_CHAT_MESSAGE + 1)}],
    }, headers={"X-Real-IP": "198.51.100.63"})
    check("an oversized history entry is refused", resp.status_code, 400)

    resp = client.post("/api/chat", json={
        "bot_id": BOT, "message": "hi", "history": "not a list",
    }, headers={"X-Real-IP": "198.51.100.64"})
    check("malformed history is refused", resp.status_code, 400)


def test_an_oversized_public_body_is_refused():
    client = make_bot()
    resp = client.post("/api/chat",
                       data=b"x" * (appmod.PUBLIC_JSON_LIMIT + 1),
                       content_type="application/json",
                       headers={"X-Real-IP": "198.51.100.65"})
    check("a body over the public cap is a 413", resp.status_code, 413)


if __name__ == "__main__":
    original_client, original_reply = appmod.get_client, appmod.generate_reply
    appmod.get_client = lambda: object()
    appmod.generate_reply = lambda bot, message, history, cid: ("ok", cid, None)
    try:
        test_a_caller_cannot_loop_the_widget()
        test_a_turn_and_its_history_are_length_checked()
        test_an_oversized_public_body_is_refused()
    finally:
        appmod.get_client, appmod.generate_reply = original_client, original_reply
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nPUBLIC LIMITS OK - bounded turns, history, loop and body")
