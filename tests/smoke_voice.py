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
import llm  # noqa: E402
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
    check("it asks for the user's words", setup.get("inputAudioTranscription"), {})
    check("it asks for its own words", setup.get("outputAudioTranscription"), {})
    check("turn-taking stays automatic",
          setup["realtimeInputConfig"]["automaticActivityDetection"]["disabled"], False)
    text = setup["systemInstruction"]["parts"][0]["text"]
    check("the instruction forbids claiming work it did not do",
          "Never claim work is done" in text, True)
    check("and forbids inventing credentials", "Never invent" in text, True)


def test_the_setup_can_be_diagnosed():
    """A wrong model or a retired id is the quiet failure; these pin the visible parts."""
    check("the default live model is the current one", voice.DEFAULT_MODEL, "gemini-3.8-live")
    check("the default is not retired", voice.DEFAULT_MODEL in voice.RETIRED_MODELS, False)
    check("the socket is the constrained one",
          "BidiGenerateContentConstrained" in voice.socket_url(), True)
    check("the socket names its api version",
          voice.api_version() in voice.socket_url(), True)
    check("the instruction knows the client",
          "Acme" in voice.setup(org_name="Acme")["setup"]["systemInstruction"]["parts"][0]["text"],
          True)
    check("the instruction knows the screen",
          "forms" in voice.setup(page="forms")["setup"]["systemInstruction"]["parts"][0]["text"],
          True)

    import settings  # noqa: E402 - the panel default must not drift from the code

    check("the settings default matches the code",
          settings.FIELDS["GEMINI_LIVE_MODEL"]["default"], voice.DEFAULT_MODEL)

    original = voice._setting
    try:
        voice._setting = lambda name, default="": (
            "gemini-2.5-flash-live" if name == "GEMINI_LIVE_MODEL" else original(name, default))
        check("a retired saved model is not sent", voice.model(), voice.DEFAULT_MODEL)
        check("and the console says why", "retired" in voice.model_note(), True)
    finally:
        voice._setting = original



def test_live_model_version_checks():
    """The API version a model is listed under must match the version the socket uses."""
    original_live = voice.live_models
    original_model = voice.model
    original_version = voice.api_version
    try:
        def fake_live(version=None, timeout=12, force=False):
            version = version or voice.api_version()
            return {"ok": True, "version": version, "error": "",
                    "models": {"v1alpha": ["gemini-3.8-live"],
                               "v1beta": ["gemini-3.8-live", "gemini-other-live"]
                               }.get(version, [])}

        voice.live_models = fake_live
        voice.api_version = lambda: "v1alpha"
        voice.model = lambda: "gemini-3.8-live"
        state = voice.model_availability()
        check("the configured live model is available", state["ok"], True)
        check("and the check really ran", state["checked"], True)

        voice.model = lambda: "gemini-3.8-flash"
        state = voice.model_availability()
        check("a text-only model is refused", state["ok"], False)
        check("the refusal lists the available Live models",
              "gemini-3.8-live" in state["error"], True)

        def version_split(version=None, timeout=12, force=False):
            version = version or voice.api_version()
            return {"ok": True, "version": version, "error": "",
                    "models": (["gemini-3.8-live"] if version == "v1beta"
                               else ["another-live"])}

        voice.live_models = version_split
        voice.model = lambda: "gemini-3.8-live"
        state = voice.model_availability()
        check("a model listed only on v1beta is refused on v1alpha", state["ok"], False)
        check("the refusal names the version to switch to",
              state["elsewhere"], ["v1beta"])
        check("and says it is listed there", "listed on v1beta" in state["error"], True)
        both = voice.live_models_by_version()
        check("both versions are inspected", sorted(both["versions"]), ["v1alpha", "v1beta"])

        def alpha_fails(version=None, timeout=12, force=False):
            version = version or voice.api_version()
            if version == "v1alpha":
                return {"ok": False, "models": [], "version": version,
                        "error": "Gemini refused the v1alpha model list (404)"}
            return {"ok": True, "models": ["gemini-3.8-live"], "version": version,
                    "error": ""}

        voice.live_models = alpha_fails
        state = voice.model_availability()
        check("a failed session-version list still checks the other version",
              state["checked"], True)
        check("and refuses when the model is listed on the other one",
              state["ok"], False)
        check("the failed list is explained in the refusal",
              "model list failed" in state["error"], True)
    finally:
        voice.live_models = original_live
        voice.model = original_model
        voice.api_version = original_version


def test_settings_voice_check():
    """Settings -> Voice -> Test names the version and checks the token mint."""
    import settings

    original_key = voice.api_key
    original_status = voice.status
    original_probe = voice.live_models_by_version
    original_mint = voice.mint
    try:
        voice.api_key = lambda: "test-key"
        voice.status = lambda *args, **kwargs: {
            "model": "gemini-3.8-live", "api_version": "v1alpha",
            "voice": "Kore", "model_note": "",
        }
        voice.live_models_by_version = lambda *args, **kwargs: {
            "versions": {
                "v1alpha": {"ok": True, "models": ["gemini-3.8-live"]},
                "v1beta": {"ok": True, "models": ["gemini-3.8-live"]},
            }
        }
        voice.mint = lambda: "ephemeral-token"
        result = settings._test_voice()
        check("the voice Test passes when the model and token work", result["ok"], True)
        check("it names the session version", "v1alpha Live models" in result["output"], True)
        check("it checks the token mint", "Session token mint: OK" in result["output"], True)

        voice.live_models_by_version = lambda *args, **kwargs: {
            "versions": {
                "v1alpha": {"ok": True, "models": ["another-live"]},
                "v1beta": {"ok": True, "models": ["gemini-3.8-live"]},
            }
        }
        result = settings._test_voice()
        check("a version mismatch fails the Test", result["ok"], False)
        check("and names the version to switch to",
              "listed on v1beta" in result["output"], True)
    finally:
        voice.api_key = original_key
        voice.status = original_status
        voice.live_models_by_version = original_probe
        voice.mint = original_mint


def test_the_routes():
    client = appmod.app.test_client()
    check("status needs a login", client.get("/api/voice/status").status_code, 401)
    client.post("/api/auth/login", json={"email": "admin@example.com", "password": "admin123"})

    original_key, original_mint = voice.api_key, voice.mint
    original_availability = voice.model_availability
    try:
        voice.api_key = lambda: ""
        refused = client.post("/api/voice/session", json={"page": "forms"})
        check("a session without a key is refused", refused.status_code, 400)
        check("with a fallback flag for the UI",
              (refused.get_json() or {}).get("fallback"), "text")

        voice.api_key = lambda: "test-key"
        voice.mint = lambda: "ephemeral-token"
        voice.model_availability = lambda: {
            "ok": True, "checked": True, "model": voice.model(),
            "version": voice.api_version(), "available_models": [voice.model()]}
        session = client.post("/api/voice/session", json={"page": "forms"})
        data = session.get_json() or {}
        check("a session mints a token", data.get("token"), "ephemeral-token")
        check("the socket endpoint is the constrained one",
              "BidiGenerateContentConstrained" in (data.get("endpoint") or ""), True)
        check("the session names the model it will use", data.get("model"), voice.model())
        check("the session names its api version", data.get("api_version"), voice.api_version())
        check("the setup carries the tool",
              data["setup"]["setup"]["tools"][0]["functionDeclarations"][0]["name"],
              "delegate_to_team")

        voice.model_availability = lambda: {
            "ok": False, "checked": True, "model": voice.model(),
            "version": voice.api_version(), "available_models": [],
            "elsewhere": ["v1beta"],
            "error": ("The live model %s is not available on %s, but it is listed on "
                      "v1beta. Open Settings -> Voice -> Test." %
                      (voice.model(), voice.api_version()))}
        blocked = client.post("/api/voice/session", json={})
        check("a model absent from the session API version is refused before minting",
              blocked.status_code, 400)
        body = blocked.get_json() or {}
        check("the preflight explains the version mismatch",
              "listed on v1beta" in body.get("error", ""), True)
        check("it offers the text fallback", body.get("fallback"), "text")
        check("and does not hand out a token", body.get("token"), None)

        voice.model_availability = lambda: {
            "ok": True, "checked": True, "model": voice.model(),
            "version": voice.api_version(), "available_models": [voice.model()]}

        def boom():
            raise voice.VoiceError("Gemini refused to mint a session token (403): denied")

        voice.mint = boom
        failed = client.post("/api/voice/session", json={})
        check("a mint failure is a 502, not a crash", failed.status_code, 502)
        check("and the message is passed through",
              "refused" in (failed.get_json() or {}).get("error", ""), True)
    finally:
        voice.api_key, voice.mint = original_key, original_mint
        voice.model_availability = original_availability

    bad = client.post("/api/voice/tool", json={"name": "create_form", "args": {}})
    check("only delegate_to_team may be called", bad.status_code, 403)
    empty = client.post("/api/voice/tool", json={"name": "delegate_to_team", "args": {}})
    check("an empty instruction is refused", empty.status_code, 400)

    # The delegation route now refuses before creating a job when no chat provider is
    # configured; this test is about the route, not the deployment's keys, so provide one.
    original_providers = llm.configured_providers
    llm.configured_providers = lambda: ["deepseek"]

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
    llm.configured_providers = original_providers


def test_the_browser_half_is_wired():
    """Static: the server half is useless if the page never loads the microphone client."""
    def read(*parts):
        return open(os.path.join(ROOT, *parts), encoding="utf-8").read()

    client_js = read("admin", "voice.js")
    admin_js = read("admin", "admin.js")
    page = read("admin", "index.html")
    css = read("admin", "admin.css")

    # The gate. onopen may only say "connecting"; setupComplete is what listens.
    onopen = client_js.split("ws.onopen = function", 1)[1].split("ws.onmessage", 1)[0]
    setup_handler = client_js.split("function handleSetupComplete", 1)[1].split(
        "function handleServerContent", 1)[0]
    check("it sends the setup message on open", "send(data.setup)" in onopen, True)
    check("opening the socket is not listening", "listening" not in onopen, True)
    check("setupComplete is the gate to listening",
          "setPhase('listening'" in setup_handler, True)
    check("the client has a setup timeout", "SETUP_TIMEOUT" in client_js, True)
    check("the timeout points at the Voice Test",
          "Settings -> Voice -> Test" in client_js, True)

    # Every silent failure now has a visible branch.
    check("the client shows the server's error", "message.error" in client_js, True)
    check("the client handles goAway", "goAway" in client_js, True)
    check("the client reads the close reason", "event.reason" in client_js, True)
    check("a close before setup says rejected", "was rejected" in client_js, True)
    check("the client can copy diagnostics", "copyDiagnostics" in client_js, True)

    # The audio plumbing.
    check("mic audio is 16 kHz pcm", "audio/pcm;rate=16000" in client_js, True)
    check("barge-in silences the queue", "content.interrupted" in client_js, True)
    check("a tool call is relayed to our route", "'/api/voice/tool'" in client_js, True)
    check("the mic is asked for before a token is minted",
          client_js.index("getUserMedia") < client_js.index("/api/voice/session"), True)
    check("both sides are analysed", "createAnalyser" in client_js, True)
    check("the audio context is resumed", ".resume()" in client_js, True)
    check("a finished turn returns to listening", "turnComplete" in client_js, True)
    check("turns are finalised once", "finalizeTurn" in client_js, True)

    # One voice screen, opened from the sheet (and the two other doors).
    check("the sheet opens the overlay", "source: 'sheet'" in admin_js, True)
    check("the assistant page opens it too", "source: 'assistant'" in admin_js, True)
    check("the tour opens it too", "source: 'tour'" in admin_js, True)
    check("all three use one entry point", admin_js.count("AscendVoice.open(") >= 3, True)
    check("the old inline hooks are gone", "onHeard" not in admin_js, True)
    check("the assistant only offers the mic when allowed",
          "'/api/voice/status'" in admin_js, True)
    check("the page loads the client", "/admin/voice.js" in page, True)

    # The orb is a measurement, not a decoration.
    check("the orb reads a level", "--voice-level" in client_js, True)
    check("and invents nothing", "Math.random" not in client_js, True)
    check("the overlay is styled", ".voice-overlay" in css and ".voice-orb" in css, True)
    check("the speaking colour is a token", "--voice-speaking" in css, True)
    check("the sheet remains for text", ".assist-sheet" in css, True)

    check("the orb and the tour are booted with the shell",
          "assistOrb();" in admin_js and "startTour(false);" in admin_js, True)
    check("the tour remembers being seen", "'tour:' +" in admin_js, True)
    check("the sheet sends page context with the question",
          "the user is on the '" in admin_js, True)
    check("and names the record when there is one",
          "screen, record #" in admin_js, True)


if __name__ == "__main__":
    test_status_tells_the_truth()
    test_setup_declares_one_tool()
    test_the_setup_can_be_diagnosed()
    test_live_model_version_checks()
    test_settings_voice_check()
    test_the_routes()
    test_the_browser_half_is_wired()
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nVOICE OK - the key stays here, the token expires, and the voice can only delegate")
