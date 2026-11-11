"""The first-run welcome: once per account, and the client asks for it.

Run: .\\venv\\Scripts\\python.exe tests\\smoke_onboarding.py
"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="onboarding-"), "test.db").replace("\\", "/")
os.environ.pop("ALLOW_LIVE_ACTIONS", None)
os.environ.pop("JOB_BACKEND", None)
os.environ.pop("REDIS_URL", None)

import app as appmod  # noqa: E402

FAILED = []


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


def test_the_flag_is_per_account():
    client = appmod.app.test_client()
    check("marking onboarding needs a login",
          client.post("/api/auth/onboarded").status_code, 401)
    login = client.post("/api/auth/login", json={"email": "admin@example.com",
                                                "password": "admin123"})
    check("the operator can sign in", login.status_code, 200)
    me = client.get("/api/auth/me").get_json()["user"]
    check("a fresh account has not seen the welcome", me.get("onboarded_at"), None)

    first = client.post("/api/auth/onboarded").get_json().get("onboarded_at")
    check("marking records a timestamp", bool(first), True)
    again = client.post("/api/auth/onboarded").get_json().get("onboarded_at")
    check("the first timestamp wins", again, first)
    after = client.get("/api/auth/me").get_json()["user"]
    check("and /me carries it", after.get("onboarded_at"), first)


def test_the_client_asks_for_it_once():
    admin_js = open(os.path.join(ROOT, "admin", "admin.js"), encoding="utf-8").read()
    voice_js = open(os.path.join(ROOT, "admin", "voice.js"), encoding="utf-8").read()

    check("the boot has a first-run step", "function firstRun(" in admin_js, True)
    check("it is called after login", "render(); firstRun();" in admin_js, True)
    check("it is called after boot", "firstRun();\n  }).catch" in admin_js, True)
    check("it records the flag on the account", "/api/auth/onboarded" in admin_js, True)
    check("it keys on the server flag", "state.user.onboarded_at" in admin_js, True)
    check("it opens the voice with the intro", "intro: true" in admin_js, True)
    check("there is a text fallback", "function showTextIntro(" in admin_js, True)
    check("the tour follows the welcome", "var after = function () { startTour(false); };" in admin_js,
          True)

    check("the voice asks the server for the intro", "intro: !!(state.opts && state.opts.intro)"
          in voice_js, True)
    check("the session sends one kickoff", "function maybeStartIntro(" in voice_js, True)
    check("and cannot send it twice", "state.introSent" in voice_js, True)
    check("a suspended context waits for a tap", "paused audio" in voice_js, True)


if __name__ == "__main__":
    test_the_flag_is_per_account()
    test_the_client_asks_for_it_once()
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nONBOARDING OK - one spoken welcome per account, with a text fallback")
