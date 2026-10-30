"""The spoken assistant: Gemini is the mouth, our agents are the hands.

The browser talks to Google directly, using an ephemeral token we mint, so the API key never
leaves this server and no WebSocket is held open by a WSGI worker. The live model gets exactly
one function - ``delegate_to_team`` - which hands the work to a normal manager job. Everything
that guards a typed request therefore guards a spoken one unchanged: the tenant comes from the
session, the tool list is the granted one, proposals never self-apply, and an outward action
still stops at a gate.

Audio is never stored. What is kept is the transcript, which is the same text the thread shows.
"""

import datetime
import json
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

# Ephemeral tokens are the one part of the Live API the installed google-genai SDK still
# pins to v1alpha (live.py: "The SDK's ephemeral token support is in v1alpha only").
# The public docs show v1beta, so the version is a setting rather than a hardcode: a key
# that only answers on v1beta can be switched without a code change.
API_VERSION = "v1alpha"
API_VERSIONS = ("v1alpha", "v1beta")

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
MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"


def _post(url, payload, timeout=15):
    body = json.dumps(payload).encode()
    request = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
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


def mint():
    """An ephemeral token the browser opens a Live session with.

    ``uses=1`` and a minute of expiry mean a leaked token is worth almost nothing: one session,
    very briefly. The API key itself never leaves this server.
    """
    key = api_key()
    if not key:
        raise VoiceError("No Google Gemini key is configured.")
    payload = {"uses": 1, "expireTime": _stamp(SESSION_SECONDS + 60),
               "newSessionExpireTime": _stamp(60)}
    url = (AUTH_TOKENS_URL % api_version()) + "?key=" + urllib.parse.quote(key)
    data = _post(url, payload)
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


def setup(org_name="", page=""):
    """The first message the browser sends once the socket opens.

    Two things here are not optional if the console is to be usable:

    * ``inputAudioTranscription`` and ``outputAudioTranscription`` - without them a
      perfectly working session produces no text at all, so neither the user nor the
      Support conversation can tell it apart from a dead one.
    * ``realtimeInputConfig.automaticActivityDetection`` - stated explicitly rather than
      left to the server default, so turn-taking is a decision this file made.
    """
    return {"setup": {
        "model": "models/" + model(),
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {
                "prebuiltVoiceConfig": {"voiceName": voice_name()}}}},
        "systemInstruction": {"parts": [{"text": instruction(org_name, page)}]},
        "tools": [{"functionDeclarations": [delegate_declaration()]}],
        "inputAudioTranscription": {},
        "outputAudioTranscription": {},
        "realtimeInputConfig": {
            "automaticActivityDetection": {"disabled": False}},
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


def live_models(timeout=12):
    """Which Live models this key can actually see.

    A wrong model id is the quietest failure in the whole feature: the socket opens, the
    setup is refused, and the close carries nothing useful to the user. This lets Settings
    answer the question with Google's own list instead of a guess.

    Returns ``{ok, models, error}`` and never raises.
    """
    key = api_key()
    if not key:
        return {"ok": False, "models": [], "error": "No Google Gemini key is configured."}
    url = MODELS_URL + "?pageSize=1000&key=" + urllib.parse.quote(key)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            data = json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        return {"ok": False, "models": [],
                "error": "Gemini refused the model list (%s): %s" % (exc.code, detail)}
    except Exception as exc:
        return {"ok": False, "models": [], "error": "Could not reach Gemini: %s" % exc}

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
    return {"ok": True, "models": sorted(set(found)), "error": ""}


def status(user_id=None, probe=False):
    """Whether a spoken session can start, and if not, why - as a sentence for Settings."""
    details = {
        "model": model(), "default_model": DEFAULT_MODEL, "model_note": model_note(),
        "voice": voice_name(), "api_version": api_version(), "endpoint": socket_url(),
        "transcription": True,
        "remaining_minutes": round(remaining_minutes(user_id or 0), 1),
    }
    if probe:
        details["live_models"] = live_models()
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
