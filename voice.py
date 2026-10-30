"""The spoken assistant: Gemini is the mouth, our agents are the hands.

The browser talks to Google directly, using an ephemeral token we mint, so the API key never
leaves this server and no WebSocket is held open by a WSGI worker. The live model gets exactly
one function - ``delegate_to_team`` - which hands the work to a normal manager job. Everything
that guards a typed request therefore guards a spoken one unchanged: the tenant comes from the
session, the tool list is the granted one, proposals never self-apply, and an outward action
still stops at a gate.

Audio is never stored. What is kept is the transcript, which is the same text the thread shows.
"""

import asyncio
import datetime
import json
import time
import urllib.error
import urllib.parse
import urllib.request

# The Live model this build speaks through. Gemini 3.8 Live is the current default Live
# model; the 2.5 live ids are retired. A key provisioned differently can point at another
# model with GEMINI_LIVE_MODEL, and Settings -> Voice -> Test lists what the key can see.
DEFAULT_MODEL = "gemini-3.8-live"
RETIRED_MODELS = {
    "gemini-2.5-flash-live",
    "gemini-live-2.5-flash-preview",
    "gemini-2.5-flash-preview-native-audio-dialog",
    "gemini-2.5-flash-exp-native-audio-thinking-dialog",
}
DEFAULT_VOICE = "Kore"
DEFAULT_MINUTES = 10

# Ephemeral tokens are documented for the v1beta Live API. The installed
# google-genai SDK still carries an older "v1alpha only" experiment warning, but a
# v1alpha auth token could be minted and then never complete a constrained session.
# The raw-WebSocket path here follows the documented v1beta ephemeral-token flow.
API_VERSION = "v1beta"
API_VERSIONS = ("v1beta",)

# Minutes used today, per user, counted in this process. A restart forgives the day's usage -
# deliberately simple, and honest about it: the token mint is still the gate, and the real
# cost control is the per-session cap below.
_USED = {}
SESSION_SECONDS = 300          # five minutes per turn, "continue" to extend
SESSION_CALLS = 6              # delegations per session; a spoken loop is expensive


class VoiceError(RuntimeError):
    pass


def _setting(name, default=""):
    try:
        import settings

        return str(settings.get(name, default) or default).strip()
    except Exception:
        import os

        return os.environ.get(name, default) or ""


def api_key():
    return _setting("GEMINI_API_KEY")


def model():
    """The live model to open the socket with.

    A retired id is mapped to the current default rather than sent: the socket would be
    closed without a word, which is the failure this mapping exists to prevent.
    """
    configured = _setting("GEMINI_LIVE_MODEL", "").strip()
    if not configured or configured in RETIRED_MODELS:
        return DEFAULT_MODEL
    return configured


def model_note():
    """The sentence Settings shows when a saved model was retired under it."""
    configured = _setting("GEMINI_LIVE_MODEL", "").strip()
    if configured and configured in RETIRED_MODELS:
        return ("The saved live model %s is retired, so %s is being used instead. "
                "Clear the setting to make that permanent." % (configured, DEFAULT_MODEL))
    return ""


def api_version():
    value = _setting("GEMINI_LIVE_API_VERSION", API_VERSION).strip().lower()
    return value if value in API_VERSIONS else API_VERSION


def socket_url():
    """The constrained socket: the only endpoint a one-use token may open."""
    return ("wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage"
            ".%s.GenerativeService.BidiGenerateContentConstrained" % api_version())


def voice_name():
    return _setting("GEMINI_VOICE", DEFAULT_VOICE) or DEFAULT_VOICE


def daily_minutes():
    try:
        return max(0, int(_setting("VOICE_DAILY_MINUTES", str(DEFAULT_MINUTES))))
    except ValueError:
        return DEFAULT_MINUTES


def _today():
    return datetime.date.today().isoformat()


def minutes_used(user_id):
    row = _USED.get(str(user_id)) or {}
    return float(row.get("seconds", 0)) / 60.0 if row.get("day") == _today() else 0.0


def record_use(user_id, seconds):
    key = str(user_id)
    used = _USED.get(key)
    if not used or used.get("day") != _today():
        used = {"day": _today(), "seconds": 0}
    used["seconds"] += max(0, int(seconds or 0))
    _USED[key] = used


def remaining_minutes(user_id):
    return max(0.0, daily_minutes() - minutes_used(user_id))


AUTH_TOKENS_URL = ("https://generativelanguage.googleapis.com/%s/auth_tokens")
MODELS_URL = "https://generativelanguage.googleapis.com/%s/models"
MODELS_CACHE_SECONDS = 300
_MODELS_CACHE = {}


def _post(url, payload, timeout=15, headers=None):
    body = json.dumps(payload).encode()
    request_headers = {"Content-Type": "application/json"}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, data=body, method="POST",
                                     headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise VoiceError("Gemini refused to mint a session token (%s): %s"
                         % (exc.code, detail))
    except Exception as exc:
        raise VoiceError("Could not reach Gemini: %s" % exc)


def _stamp(seconds):
    moment = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def mint_payload(live_setup=None):
    """The auth-token request for the exact Live setup the browser will send.

    A bare token is not enough: the constrained Live endpoint needs the token tied to
    the model and response modality. This mirrors the documented
    ``liveConnectConstraints`` payload and keeps the token and setup from drifting.
    """
    setup_message = live_setup or setup()["setup"]
    model_name = str(setup_message.get("model") or ("models/" + model()))
    generation = setup_message.get("generationConfig") or {}
    modalities = generation.get("responseModalities") or ["AUDIO"]
    return {
        "uses": 1,
        "expireTime": _stamp(SESSION_SECONDS + 60),
        "newSessionExpireTime": _stamp(60),
        "liveConnectConstraints": {
            "model": model_name,
            "config": {
                "sessionResumption": {},
                "responseModalities": list(modalities),
            },
        },
    }


def mint(live_setup=None):
    """An ephemeral, model-constrained token the browser opens a Live session with.

    ``uses=1`` and a minute of expiry mean a leaked token is worth almost nothing: one
    session, very briefly. The API key itself never leaves this server.
    """
    key = api_key()
    if not key:
        raise VoiceError("No Google Gemini key is configured.")
    payload = mint_payload(live_setup)
    url = AUTH_TOKENS_URL % api_version()
    data = _post(url, payload, headers={"x-goog-api-key": key})
    token = str(data.get("name") or data.get("token") or "").strip()
    if not token:
        raise VoiceError("Gemini returned no token: %s" % json.dumps(data)[:200])
    return token


def delegate_declaration():
    """The one function the voice may call. Everything else belongs to our agents."""
    return {
        "name": "delegate_to_team",
        "description": (
            "Hand a piece of work to the platform's own agents, which are the only thing that "
            "creates or changes anything: forms, templates, automations, sites, domains, "
            "contacts. They enforce the client's permissions and approval gates. Use this for "
            "any request to build, change, publish or send; use your own words only to explain, "
            "guide, or answer questions about the screen."),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "instruction": {
                    "type": "STRING",
                    "description": "What the user wants done, one sentence, in their words."}},
            "required": ["instruction"]},
    }


def live_config(org_name="", page=""):
    """The Live connection config shared by the token constraints and the setup frame."""
    return {
        "responseModalities": ["AUDIO"],
        "speechConfig": {"voiceConfig": {
            "prebuiltVoiceConfig": {"voiceName": voice_name()}}},
        "systemInstruction": {"parts": [{"text": instruction(org_name, page)}]},
        "tools": [{"functionDeclarations": [delegate_declaration()]}],
        "inputAudioTranscription": {},
        "outputAudioTranscription": {},
        "realtimeInputConfig": {
            "automaticActivityDetection": {"disabled": False}},
        "sessionResumption": {},
    }


def setup(org_name="", page=""):
    """The first message the browser sends once the socket opens.

    Two things here are not optional if the console is to be usable:

    * ``inputAudioTranscription`` and ``outputAudioTranscription`` - without them a
      perfectly working session produces no text at all, so neither the user nor the
      Support conversation can tell it apart from a dead one.
    * ``realtimeInputConfig.automaticActivityDetection`` - stated explicitly rather than
      left to the server default, so turn-taking is a decision this file made.
    """
    config = live_config(org_name, page)
    return {"setup": {
        "model": "models/" + model(),
        "generationConfig": {
            "responseModalities": config["responseModalities"],
            "speechConfig": config["speechConfig"],
        },
        "systemInstruction": config["systemInstruction"],
        "tools": config["tools"],
        "inputAudioTranscription": config["inputAudioTranscription"],
        "outputAudioTranscription": config["outputAudioTranscription"],
        "realtimeInputConfig": config["realtimeInputConfig"],
    }}


def instruction(org_name="", page=""):
    """The character: a guide who delegates, not a second brain.

    Written as a prompt rather than a rule, because the failure this prevents is a voice that
    talks confidently about work nobody did.
    """
    where = (" The user is looking at the %s screen." % page) if page else ""
    client = (" The client being worked on is %s." % org_name) if org_name else ""
    return (
        "You are the platform's spoken guide: warm, brief, concrete. Two sentences, then stop. "
        "You explain what is on screen, answer questions about it, and can walk a new user "
        "through the product one step at a time.%s%s "
        "You have exactly one tool, delegate_to_team, and nothing else. Never claim work is done "
        "until that call returns a result. If the user asks for anything to be built, changed, "
        "published or sent, call delegate_to_team with one clear sentence, then say you are on "
        "it. If the team asks a question, read it aloud and pass the answer back. Never invent "
        "prices, credentials or a domain name - those are the user's to give."
        % (client, where))


def probe_session(live_setup=None, timeout=15):
    """Open the exact constrained Live socket server-side and wait for setupComplete.

    This is the check the browser cannot give us before a user presses Talk: it proves
    the minted token, model, API version and setup frame form a real session. It sends
    no audio; the token is one-use and the connection closes immediately after the
    server's first meaningful frame.
    """
    live_setup = live_setup or setup()["setup"]
    try:
        token = mint(live_setup)
    except VoiceError as exc:
        return {"ok": False, "output": str(exc)}
    url = socket_url() + "?access_token=" + urllib.parse.quote(token)

    async def run():
        try:
            import websockets
        except ImportError:
            return {"ok": False, "output":
                    "The server's websockets package is missing, so the Live setup "
                    "probe cannot run. Install requirements.txt and redeploy."}
        try:
            async with websockets.connect(
                    url, open_timeout=min(10, timeout), close_timeout=3,
                    max_size=2 ** 20) as ws:
                await ws.send(json.dumps({"setup": live_setup}))
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    except asyncio.TimeoutError:
                        return {"ok": False, "output":
                                "The Live socket opened but no setupComplete arrived "
                                "within %d seconds." % timeout}
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", "replace")
                    try:
                        message = json.loads(raw)
                    except ValueError:
                        return {"ok": False, "output":
                                "The Live server sent a non-JSON frame: %s"
                                % str(raw)[:200]}
                    if message.get("setupComplete"):
                        session = message.get("setupComplete") or {}
                        session_id = (session.get("sessionId")
                                      if isinstance(session, dict) else "")
                        return {"ok": True, "output":
                                "setupComplete received%s."
                                % ((" (session %s)" % session_id) if session_id else "")}
                    if message.get("error"):
                        return {"ok": False, "output":
                                "The Live server refused the setup: %s"
                                % json.dumps(message["error"])[:400]}
                    if message.get("goAway"):
                        return {"ok": False, "output":
                                "The Live server sent goAway before setupComplete."}
        except Exception as exc:
            return {"ok": False, "output":
                    "The Live socket check failed: %s: %s"
                    % (type(exc).__name__, exc)}
    try:
        return asyncio.run(run())
    except RuntimeError as exc:
        return {"ok": False, "output":
                "Could not run the Live socket check: %s" % exc}
    value = str(value or "").strip().lower()
    return value if value in API_VERSIONS else API_VERSION


def _live_model_names(data):
    """The model ids in a Google models.list response that can speak Live."""
    found = []
    for row in data.get("models") or []:
        name = str(row.get("name") or "").replace("models/", "", 1).strip()
        if not name:
            continue
        actions = row.get("supportedActions") or row.get("supportedGenerationMethods") or []
        endpoints = []
        for item in row.get("endpoints") or []:
            endpoints.append(str(item.get("name") or "") if isinstance(item, dict) else str(item))
        if actions or endpoints:
            live = (any("bidiGenerateContent" in str(action) for action in actions)
                    or any("bidiGenerateContent" in endpoint for endpoint in endpoints))
        else:
            # Older API shapes omit both; the name is then the only signal available.
            live = "live" in name
        if live:
            found.append(name)
    return sorted(set(found))


def _live_models_for(version, timeout=12, force=False):
    """Cached models.list for one API version. Returns {ok, models, version, error}."""
    version = _normalise_version(version)
    key = api_key()
    if not key:
        return {"ok": False, "models": [], "version": version,
                "error": "No Google Gemini key is configured."}
    cache_key = (key, version)
    cached = _MODELS_CACHE.get(cache_key)
    if cached and not force and (time.time() - cached[0]) < MODELS_CACHE_SECONDS:
        return dict(cached[1])
    url = (MODELS_URL % version) + "?pageSize=1000&key=" + urllib.parse.quote(key)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            data = json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        return {"ok": False, "models": [], "version": version,
                "error": "Gemini refused the %s model list (%s): %s"
                         % (version, exc.code, detail)}
    except Exception as exc:
        return {"ok": False, "models": [], "version": version,
                "error": "Could not reach Gemini for the %s model list: %s"
                         % (version, exc)}
    result = {"ok": True, "models": _live_model_names(data), "version": version,
              "error": ""}
    _MODELS_CACHE[cache_key] = (time.time(), result)
    return dict(result)


def live_models(version=None, timeout=12, force=False):
    """Which Live models this key can actually see on one API version."""
    return _live_models_for(version or api_version(), timeout, force)


def live_models_by_version(timeout=12, force=False):
    """The Live model list for the v1beta ephemeral-token API."""
    versions = {version: live_models(version, timeout, force) for version in API_VERSIONS}
    return {
        "ok": any(info.get("ok") for info in versions.values()),
        "models": sorted({model_id for info in versions.values()
                          for model_id in (info.get("models") or [])}),
        "versions": versions,
        "error": " ".join(info["error"] for info in versions.values()
                          if info.get("error")),
    }


def model_availability(force=False, timeout=6):
    """Whether the configured Live model is listed for the v1beta ephemeral-token API.

    A model listed for the key is not proof that a constrained socket will accept the
    setup. The Settings Test uses probe_session() for that; this preflight only blocks
    obvious model mismatches. It returns ``checked=False`` when Google cannot be
    reached, so a transient network failure never makes the microphone disappear.
    """
    wanted = model()
    session_version = api_version()
    current = live_models(session_version, timeout=timeout, force=force)
    base = {
        "model": wanted,
        "version": session_version,
        "available_models": current.get("models") or [],
        "live_error": current.get("error") or "",
    }
    if not current.get("ok"):
        other_versions = {}
        elsewhere = []
        for version in API_VERSIONS:
            if version == session_version:
                continue
            info = live_models(version, timeout=timeout, force=force)
            other_versions[version] = info
            if info.get("ok") and wanted in (info.get("models") or []):
                elsewhere.append(version)
        if elsewhere:
            error = (
                "The %s model list failed (%s), but the live model %s is listed on %s. "
                "Switch Live API version to %s, or choose a model that %s lists. "
                "Open Settings -> Voice -> Test." %
                (session_version, current.get("error") or "unknown error", wanted,
                 ", ".join(elsewhere), elsewhere[0], session_version))
            return dict(base, ok=False, checked=True, error=error,
                        other_versions=other_versions, elsewhere=elsewhere)
        return dict(base, ok=True, checked=False, error="")
    if wanted in (current.get("models") or []):
        return dict(base, ok=True, checked=True, error="")

    other_versions = {}
    elsewhere = []
    for version in API_VERSIONS:
        if version == session_version:
            continue
        info = live_models(version, timeout=timeout, force=force)
        other_versions[version] = info
        if info.get("ok") and wanted in (info.get("models") or []):
            elsewhere.append(version)

    if elsewhere:
        error = (
            "The live model %s is not available on %s, but it is listed on %s. "
            "Switch Live API version to %s, or choose a model that %s lists. "
            "Open Settings -> Voice -> Test." %
            (wanted, session_version, ", ".join(elsewhere), elsewhere[0], session_version))
    else:
        available = ", ".join((base["available_models"] or [])[:8]) or "none"
        error = (
            "The live model %s is not available on %s. Live models on %s: %s. "
            "Open Settings -> Voice -> Test; the Live model is separate from the "
            "Gemini text model in AI providers." %
            (wanted, session_version, session_version, available))
    return dict(base, ok=False, checked=True, error=error,
                other_versions=other_versions, elsewhere=elsewhere)


def status(user_id=None, probe=False):
    """Whether a spoken session can start, and if not, why - as a sentence for Settings."""
    details = {
        "model": model(), "default_model": DEFAULT_MODEL, "model_note": model_note(),
        "voice": voice_name(), "api_version": api_version(), "endpoint": socket_url(),
        "transcription": True,
        "remaining_minutes": round(remaining_minutes(user_id or 0), 1),
    }
    if probe:
        probe_all = live_models_by_version()
        details["live_models"] = probe_all["versions"].get(
            api_version(), {"ok": False, "models": []})
        details["live_versions"] = probe_all
    key = api_key()
    if not key:
        return dict(details, available=False, remaining_minutes=0,
                    reason="No Google Gemini key is configured. Add one in Settings -> AI "
                           "providers, and the microphone appears.")
    if user_id is not None and remaining_minutes(user_id) <= 0:
        return dict(details, available=False, remaining_minutes=0,
                    reason="Today's %d voice minutes are used up. Typing still works."
                           % daily_minutes())
    return dict(details, available=True, reason="",
                session_seconds=SESSION_SECONDS, session_calls=SESSION_CALLS)
