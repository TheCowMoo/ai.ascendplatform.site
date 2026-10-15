"""
Operator-managed settings (stage S6).

The admin Settings tab writes to the ``settings`` table; :func:`get` layers that
over the process environment, resolving left to right:

    panel value  ->  environment variable  ->  SPEC default

Why the panel wins: it matches the per-channel Meta override that already exists
("a secret set in the admin panel overrides the globals"), and it is the only way
to reconfigure a running container, where ``env_file`` was read at start-up.

Three rules keep this safe:

* **Only keys in SPEC are writable.** Arbitrary environment mutation from a
  request handler is a code-execution vector (``PATH``, ``LD_PRELOAD``), so an
  unknown key is rejected rather than stored.
* **Secrets are write-only.** :func:`all_for_ui` reports whether a secret is set,
  where its value came from and a short tail - never the value itself.
* **Changes are audited.** Every write records the actor and key in
  ``settings_events``, and never the secret value.

Values are read per call, so a field marked ``applies: "live"`` takes effect
without a restart. A short TTL cache keeps the lookups off the hot path while
still letting another process (the arq worker) see a change.

``DATABASE_URL`` and ``SECRET_KEY`` are deliberately absent: they bootstrap the
connection and the session signing, so they belong to `.env`/compose.
"""

import os
import threading
import time

from db import get_connection, now_str

# A panel write in one process has to become visible to the others (web + worker),
# which a per-process cache cannot do on its own. A few seconds of staleness is a
# better trade than a database query on every tool call.
CACHE_TTL = float(os.environ.get("SETTINGS_CACHE_TTL") or 3.0)

LIVE = "live"
RESTART = "restart"


def _f(key, label, **kw):
    """One settings field, carrying the defaults every field shares."""
    field = {
        "key": key,
        "label": label,
        "kind": "text",       # text | int | bool | choice
        "secret": False,
        "default": "",
        "help": "",
        "applies": LIVE,
        "choices": (),
        "risky": False,
        "readonly": False,
    }
    field.update(kw)
    return field


SPEC = [
    {
        "id": "llm",
        "label": "AI providers",
        "icon": "🧠",
        "blurb": "Credentials for the models the agents run on. Applied immediately.",
        "test": "llm",
        "fields": [
            _f("DEEPSEEK_API_KEY", "DeepSeek API key", secret=True,
               help="The default provider for every agent."),
            _f("DEEPSEEK_BASE_URL", "DeepSeek base URL",
               default="https://api.deepseek.com"),
            _f("MODEL", "DeepSeek model", default="deepseek-chat"),
            _f("ANTHROPIC_API_KEY", "Anthropic API key", secret=True),
            _f("ANTHROPIC_MODEL", "Claude model", default="claude-sonnet-4-5"),
            _f("GEMINI_API_KEY", "Google Gemini API key", secret=True,
               help="Also used by the Image Artist for generate_image."),
            _f("GEMINI_MODEL", "Gemini model", default="gemini-2.5-flash"),
            _f("GEMINI_IMAGE_MODEL", "Gemini image model",
               default="gemini-2.5-flash-image"),
        ],
    },
    {
        "id": "pexels",
        "label": "Stock photography",
        "icon": "📷",
        "blurb": "Lets the agents pull real photos into the content they build.",
        "test": "pexels",
        "fields": [
            _f("PEXELS_API_KEY", "Pexels API key", secret=True,
               help="Free key from pexels.com/api."),
            _f("PEXELS_PER_PAGE", "Results per search", kind="int", default="5",
               help="Between 1 and 80."),
            _f("PEXELS_TIMEOUT", "Request timeout (seconds)", kind="int",
               default="20"),
            _f("PEXELS_MAX_BYTES", "Maximum download size (bytes)", kind="int",
               default="8000000"),
        ],
    },
    {
        "id": "actions",
        "label": "Safety & approvals",
        "icon": "🛡️",
        "blurb": "How far the agents may go on their own.",
        "fields": [
            _f("ALLOW_LIVE_ACTIONS", "Allow live actions", kind="bool", default="0",
               risky=True,
               help="Off = dry-run: writes, SSH and publishing are simulated. "
                    "On = the agents act for real, each action awaiting approval."),
            _f("APPROVAL_TIMEOUT", "Approval timeout (seconds)", kind="int",
               default="300"),
        ],
    },
    {
        "id": "email",
        "label": "Email (SMTP)",
        "icon": "✉️",
        "blurb": "How the team sends mail.",
        "test": "smtp",
        "fields": [
            _f("SMTP_HOST", "SMTP host"),
            _f("SMTP_PORT", "SMTP port", kind="int", default="587"),
            _f("SMTP_USER", "SMTP username"),
            _f("SMTP_PASS", "SMTP password", secret=True),
            _f("SMTP_FROM", "From address", help="Defaults to the username."),
            _f("SMTP_TLS", "Use STARTTLS", kind="bool", default="1"),
        ],
    },
    {
        "id": "hosting",
        "label": "Servers & hosting",
        "icon": "🖥️",
        "blurb": "Limits applied whenever the Operator reaches a server.",
        "fields": [
            _f("SSH_TIMEOUT", "SSH connect timeout (seconds)", kind="int",
               default="15"),
            _f("SSH_CMD_TIMEOUT", "SSH command timeout (seconds)", kind="int",
               default="60"),
            _f("SSH_MAX_OUTPUT", "Maximum SSH output (bytes)", kind="int",
               default="40000"),
        ],
    },
    {
        "id": "app",
        "label": "App & branding",
        "icon": "🎛️",
        "blurb": "Presentation and platform limits.",
        "fields": [
            _f("BRAND_NAME", "Product name", default="CMGrowthAI"),
            _f("MAX_KB_CHARS", "Knowledge-base cap (characters)", kind="int",
               default="30000"),
            _f("GRAPH_API_VERSION", "Meta Graph API version", default="v21.0"),
        ],
    },
    {
        "id": "infra",
        "label": "Infrastructure",
        "icon": "🔧",
        "blurb": "Structural settings - they choose the topology (which queue, "
                 "where artifacts live), so they cannot change under a running "
                 "process. Shown for visibility: edit .env and restart instead.",
        "fields": [
            _f("ARTIFACTS_DIR", "Artifacts directory", applies=RESTART,
               readonly=True),
            _f("REDIS_URL", "Redis URL", applies=RESTART, readonly=True),
            _f("JOB_BACKEND", "Job backend", applies=RESTART, readonly=True),
            _f("LLM_GATEWAY_URL", "LiteLLM gateway URL", applies=RESTART,
               readonly=True),
            _f("DATABASE_URL", "Database URL", applies=RESTART, readonly=True),
            _f("HOST", "Bind host", applies=RESTART, readonly=True),
            _f("PORT", "Bind port", applies=RESTART, readonly=True),
        ],
    },
]

# key -> field (+ its group), built once so validation and the UI share one source.
FIELDS = {}
for _group in SPEC:
    for _field in _group["fields"]:
        _entry = dict(_field)
        _entry["group_id"] = _group["id"]
        _entry["group_label"] = _group["label"]
        _entry["group_test"] = _group.get("test")
        FIELDS[_entry["key"]] = _entry


class SettingsError(Exception):
    """Raised for unknown keys, read-only keys and invalid values."""


_cache = {}
_lock = threading.Lock()


def _panel_value(key):
    """The panel-stored value for ``key``, or None. TTL-cached."""
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
    value = row["value"] if row else None
    with _lock:
        _cache[key] = (now + CACHE_TTL, value)
    return value


def forget(key=None):
    """Drop cached values: one key, or all of them."""
    with _lock:
        if key is None:
            _cache.clear()
        else:
            _cache.pop(key, None)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def get(key, default=""):
    """Effective value for ``key``: panel, else environment, else SPEC default."""
    panel = _panel_value(key)
    if panel:
        return panel
    env = (os.environ.get(key) or "").strip()
    if env:
        return env
    field = FIELDS.get(key)
    if field and field["default"]:
        return field["default"]
    return default


def get_int(key, default=0):
    """Effective integer, falling back to ``default`` when unusable."""
    try:
        return int(str(get(key) or "").strip())
    except (TypeError, ValueError):
        return default


def get_bool(key, default=False):
    """Effective boolean. Accepts 1/true/yes/on and 0/false/no/off."""
    raw = str(get(key) or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def source_of(key):
    """Where the effective value came from: panel, environment or unset."""
    if _panel_value(key):
        return "panel"
    if (os.environ.get(key) or "").strip():
        return "environment"
    return "unset"


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
def mask(value, tail=4):
    """A safe preview of a secret: bullets plus the last few characters."""
    value = (value or "").strip()
    if not value:
        return "(empty)"
    if len(value) <= tail:
        return "*" * len(value)
    return "\u2022" * 4 + value[-tail:]


def _validate(field, value):
    value = "" if value is None else str(value).strip()
    kind = field["kind"]
    if kind == "int":
        if not value:
            return ""
        try:
            number = int(value)
        except ValueError:
            raise SettingsError("%s must be a whole number" % field["label"])
        if number < 0:
            raise SettingsError("%s cannot be negative" % field["label"])
        return str(number)
    if kind == "bool":
        low = value.lower()
        if low in ("1", "true", "yes", "on"):
            return "1"
        if low in ("0", "false", "no", "off", ""):
            return "0"
        raise SettingsError("%s must be on or off" % field["label"])
    if kind == "choice":
        if value and value not in field["choices"]:
            raise SettingsError(
                "%s must be one of: %s"
                % (field["label"], ", ".join(field["choices"]))
            )
        return value
    return value


def _detail(field, value):
    """What to record in the audit trail: a mask for secrets, never the value."""
    if not value:
        return "cleared"
    if field["secret"]:
        return "set (%s)" % mask(value)
    return "set to %r" % value


def set_value(key, value, actor=""):
    """Store one panel value, audit it and drop the cache. Returns the key."""
    field = FIELDS.get(key)
    if field is None:
        raise SettingsError("unknown setting %r" % key)
    if field.get("readonly"):
        raise SettingsError("%s is structural - edit .env and restart instead" % key)
    value = _validate(field, value)
    detail = _detail(field, value)
    is_secret = 1 if field["secret"] else 0
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT key FROM settings WHERE key = ?", (key,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE settings SET value = ?, is_secret = ?, updated_at = ?, "
                "updated_by = ? WHERE key = ?",
                (value, is_secret, now_str(), actor, key),
            )
        else:
            conn.execute(
                "INSERT INTO settings (key, value, is_secret, updated_at, "
                "updated_by) VALUES (?, ?, ?, ?, ?)",
                (key, value, is_secret, now_str(), actor),
            )
        conn.execute(
            "INSERT INTO settings_events (key, action, actor, detail) "
            "VALUES (?, ?, ?, ?)",
            (key, "set", actor, detail),
        )
    forget(key)
    return key


def clear_value(key, actor=""):
    """Remove a panel value so the environment (or the default) applies again."""
    field = FIELDS.get(key)
    if field is None:
        raise SettingsError("unknown setting %r" % key)
    if field.get("readonly"):
        raise SettingsError("%s is structural - edit .env and restart instead" % key)
    with get_connection() as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        conn.execute(
            "INSERT INTO settings_events (key, action, actor, detail) "
            "VALUES (?, ?, ?, ?)",
            (key, "clear", actor, "cleared; falling back to the environment"),
        )
    forget(key)
    return key


def update_many(values, clear=(), actor=""):
    """Apply a batch. A blank secret means "keep what is stored".

    The form never receives the existing secret, so an empty box has to mean
    "unchanged" - otherwise every save would wipe the key it cannot display.
    """
    changed = []
    for key, value in (values or {}).items():
        field = FIELDS.get(key)
        if field is None:
            raise SettingsError("unknown setting %r" % key)
        if field.get("readonly"):
            # Fail loudly rather than silently dropping it: the caller would
            # otherwise believe a structural value had been saved.
            raise SettingsError("%s is structural - edit .env and restart instead" % key)
        if field["secret"] and not str(value or "").strip():
            continue
        set_value(key, value, actor)
        changed.append(key)
    for key in clear or ():
        clear_value(key, actor)
        changed.append(key)
    return changed


# ---------------------------------------------------------------------------
# Admin UI payload
# ---------------------------------------------------------------------------
def _field_for_ui(field):
    key = field["key"]
    panel = _panel_value(key)
    env = (os.environ.get(key) or "").strip()
    if panel:
        origin, value = "panel", panel
    elif env:
        origin, value = "environment", env
    else:
        origin, value = "unset", ""
    out = {
        "key": key,
        "label": field["label"],
        "kind": field["kind"],
        "secret": field["secret"],
        "help": field["help"],
        "applies": field["applies"],
        "readonly": bool(field.get("readonly")),
        "risky": bool(field.get("risky")),
        "choices": list(field["choices"]),
        "default": field["default"],
        "set": bool(value),
        "source": origin,
    }
    if value and field["secret"]:
        # Never ship the value itself - only a hint that it is set.
        out["preview"] = mask(value)
    elif not field["secret"]:
        out["value"] = value
    return out


def all_for_ui():
    """Grouped settings for the admin tab. Secret values are never included."""
    groups = []
    for group in SPEC:
        groups.append({
            "id": group["id"],
            "label": group["label"],
            "icon": group["icon"],
            "blurb": group["blurb"],
            "test": group.get("test"),
            "fields": [_field_for_ui(field) for field in group["fields"]],
        })
    return groups


def history(limit=50):
    """The most recent settings changes, newest first."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, key, action, actor, detail, created_at FROM settings_events "
            "ORDER BY id DESC LIMIT %d" % max(1, int(limit))
        ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Live checks (the "Test" buttons)
# ---------------------------------------------------------------------------
def test(group_id):
    """Check one group for real. Returns ``{ok, output}`` and never raises."""
    runner = {
        "pexels": _test_pexels,
        "llm": _test_llm,
        "smtp": _test_smtp,
    }.get(group_id)
    if runner is None:
        return {"ok": False, "output": "No test available for %r." % group_id}
    try:
        return runner()
    except Exception as exc:  # pragma: no cover - defensive
        return {"ok": False, "output": "%s: %s" % (type(exc).__name__, exc)}


def _test_pexels():
    if not get("PEXELS_API_KEY"):
        return {"ok": False, "output": "No Pexels API key set yet."}
    from tools import pexels  # lazy: settings must not import tools at module level

    try:
        payload = pexels.search("office", per_page=1)
    except pexels.PexelsError as exc:
        return {"ok": False, "output": str(exc)}
    return {
        "ok": True,
        "output": "Working - %s matches for a test query."
        % payload.get("total_results", 0),
    }


def _test_llm():
    import llm  # lazy for the same reason

    if llm.gateway_enabled():
        return {
            "ok": True,
            "output": "Routed through the LiteLLM gateway at %s." % llm.LLM_GATEWAY_URL,
        }
    ready = sorted(llm.configured_providers())
    if not ready:
        return {"ok": False, "output": "No provider has a key yet."}
    return {"ok": True, "output": "Ready: %s." % ", ".join(ready)}


def _test_smtp():
    host = get("SMTP_HOST")
    if not host:
        return {"ok": False, "output": "No SMTP host set."}
    import smtplib

    port = get_int("SMTP_PORT", 587)
    user = get("SMTP_USER")
    try:
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.ehlo()
            if get_bool("SMTP_TLS", True):
                server.starttls()
                server.ehlo()
            if user:
                server.login(user, get("SMTP_PASS"))
    except Exception as exc:
        return {"ok": False, "output": "%s: %s" % (type(exc).__name__, exc)}
    return {
        "ok": True,
        "output": "Connected to %s:%d%s."
        % (host, port, " and signed in" if user else ""),
    }