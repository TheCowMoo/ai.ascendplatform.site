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
import db  # noqa: E402

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
    check("marking the Studio tour needs a login",
          client.post("/api/auth/studio-tour").status_code, 401)
    login = client.post("/api/auth/login", json={"email": "admin@example.com",
                                                "password": "admin123"})
    check("the operator can sign in", login.status_code, 200)
    me = client.get("/api/auth/me").get_json()["user"]
    check("a fresh account has not seen the welcome", me.get("onboarded_at"), None)
    check("a fresh account has not seen the Studio tour",
          me.get("studio_tour_at"), None)

    first = client.post("/api/auth/onboarded").get_json().get("onboarded_at")
    check("marking records a timestamp", bool(first), True)
    again = client.post("/api/auth/onboarded").get_json().get("onboarded_at")
    check("the first timestamp wins", again, first)
    after = client.get("/api/auth/me").get_json()["user"]
    check("and /me carries it", after.get("onboarded_at"), first)

    studio = client.post("/api/auth/studio-tour").get_json() or {}
    check("the Studio tour records a timestamp", bool(studio.get("studio_tour_at")), True)
    check("the Studio tour records its version",
          studio.get("studio_tour_version"), appmod.STUDIO_TOUR_VERSION)
    studio_again = client.post("/api/auth/studio-tour").get_json() or {}
    check("the first Studio timestamp wins",
          studio_again.get("studio_tour_at"), studio.get("studio_tour_at"))
    after = client.get("/api/auth/me").get_json()["user"]
    check("and /me carries the Studio tour",
          after.get("studio_tour_version"), appmod.STUDIO_TOUR_VERSION)

    with db.get_connection() as conn:
        conn.execute(
            "UPDATE users SET studio_tour_version = 'older', "
            "studio_tour_at = '2000-01-01 00:00:00' WHERE email = ?",
            ("admin@example.com",),
        )
    rearmed = client.post("/api/auth/studio-tour").get_json() or {}
    check("a version bump re-arms the walkthrough",
          rearmed.get("studio_tour_version"), appmod.STUDIO_TOUR_VERSION)
    check("the new version gets a new timestamp",
          rearmed.get("studio_tour_at") != "2000-01-01 00:00:00", True)


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
    check("the Studio walkthrough follows the welcome",
          "var after = function () { startStudioTour(false, null, true); };" in admin_js,
          True)
    check("the walkthrough has four Studio steps",
          admin_js.count("{ hash: '#/app/studio', target:") >= 4, True)
    check("the walkthrough has a completion card",
          "function showStudioTourComplete(" in admin_js, True)
    check("the Studio has a replay button", "How Studio works" in admin_js, True)
    check("the completion card offers the rest",
          "Show me the rest" in admin_js, True)
    check("the walkthrough is versioned",
          "STUDIO_TOUR_VERSION" in admin_js and
          "STUDIO_TOUR_VERSION" in open(os.path.join(ROOT, "app.py"),
                                        encoding="utf-8").read(), True)
    check("the walkthrough records completion on the server",
          "/api/auth/studio-tour" in admin_js, True)
    check("the Studio has stable tour hooks",
          all(hook in admin_js for hook in (
              "studio-composer", "studio-working-on", "studio-live",
              "studio-preview", "studio-chats", "studio-create")), True)

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
