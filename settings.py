"""
Operator-managed settings (stage S6).

The admin Settings tab writes to the ``settings`` table; :func:`get` layers that
over the process environment, resolving left to right:

    client value  ->  panel value  ->  environment variable  ->  SPEC default

**Scope.** Every field is either ``platform`` (the deployment's own configuration -
model keys, Pexels, the SSH limits) or ``client`` (the SMTP credentials a client
sends mail with). A client value only ever exists for a client-scoped field, and it
lives in ``client_settings`` keyed by ``(org_id, key)``. A client therefore still
*uses* the platform's ``DEEPSEEK_API_KEY`` to generate their content - they simply
cannot read it or change it, and :func:`all_for_ui` omits platform groups entirely
when an org is given.

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

# Which bucket a field belongs to. PLATFORM fields configure the deployment and
# only the operator may change them; CLIENT fields can be overridden per org.
PLATFORM = "platform"
CLIENT = "client"


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
        # None means "inherit the group's scope" - see the FIELDS build below. It
        # must not default to PLATFORM here, or every field would override the
        # client-scoped email group and no client could configure their own SMTP.
        "scope": None,
    }
    field.update(kw)
    return field


SPEC = [
    {
        "id": "llm",
        "label": "AI providers",
        "icon": "cpu",
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
        "icon": "image",
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
        "icon": "shield",
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
        "icon": "mail",
        "blurb": "How the team sends mail.",
        "test": "smtp",
        # The one client-scoped group: a client sends from their own domain, so
        # these resolve per org. Everything else here configures the deployment.
        "scope": CLIENT,
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
        "icon": "server",
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
        "icon": "sliders",
        "blurb": "Presentation and platform limits.",
        "fields": [
            _f("BRAND_NAME", "Product name", default="AscendPlatform"),
            _f("MAX_KB_CHARS", "Knowledge-base cap (characters)", kind="int",
               default="30000"),
            _f("GRAPH_API_VERSION", "Meta Graph API version", default="v21.0"),
        ],
    },
    {
        "id": "infra",
        "label": "Infrastructure",
        "icon": "wrench",
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
# The scope is resolved here and written back onto the field dicts in SPEC, so the
# panel builder sees the same value the validators do.
FIELDS = {}
for _group in SPEC:
    for _field in _group["fields"]:
        # The group decides the scope unless a field names its own.
        _field["scope"] = _field.get("scope") or _group.get("scope", PLATFORM)
        _entry = dict(_field)
        _entry["group_id"] = _group["id"]
        _entry["group_label"] = _group["label"]
        _entry["group_test"] = _group.get("test")
        FIELDS[_entry["key"]] = _entry


def group_scope(group):
    """``platform`` or ``client`` for a whole group."""
    return group.get("scope", PLATFORM)


class SettingsError(Exception):
    """Raised for unknown keys, read-only keys and invalid values."""


_cache = {}
_lock = threading.Lock()


def _cache_key(key, org_id=None):
    return "%s:%s" % (org_id if org_id is not None else "", key)


def _panel_value(key, org_id=None):
    """The panel-stored value for ``key``, or None. TTL-cached.

    ``org_id`` reads that client's override; without it this is the platform value.
    """
    cache_key = _cache_key(key, org_id)
    now = time.time()
    with _lock:
        hit = _cache.get(cache_key)
        if hit and hit[0] > now:
            return hit[1]
    with get_connection() as conn:
        if org_id is None:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT value FROM client_settings WHERE org_id = ? AND key = ?",
                (org_id, key),
            ).fetchone()
    value = row["value"] if row else None
    with _lock:
        _cache[cache_key] = (now + CACHE_TTL, value)
    return value


def forget(key=None):
    """Drop cached values: every scope of one key, or all of them."""
    with _lock:
        if key is None:
            _cache.clear()
            return
        suffix = ":" + key
        for cache_key in [k for k in _cache if k.endswith(suffix)]:
            _cache.pop(cache_key, None)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def get(key, default="", org_id=None):
    """Effective value for ``key``.

    Resolution: the client's override (client-scoped fields only), else the panel,
    else the environment, else the SPEC default. A client still *uses* a platform
    key such as ``DEEPSEEK_API_KEY`` - it just cannot read or change it.
    """
    field = FIELDS.get(key)
    if org_id is not None and field and field["scope"] == CLIENT:
        value = _panel_value(key, org_id)
        if value:
            return value
    panel = _panel_value(key)
    if panel:
        return panel
    env = (os.environ.get(key) or "").strip()
    if env:
        return env
    if field and field["default"]:
        return field["default"]
    return default


def get_int(key, default=0, org_id=None):
    """Effective integer, falling back to ``default`` when unusable."""
    try:
        return int(str(get(key, org_id=org_id) or "").strip())
    except (TypeError, ValueError):
        return default


def get_bool(key, default=False, org_id=None):
    """Effective boolean. Accepts 1/true/yes/on and 0/false/no/off."""
    raw = str(get(key, org_id=org_id) or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def source_of(key, org_id=None):
    """Where the effective value came from: client, panel, environment or unset."""
    field = FIELDS.get(key)
    if (org_id is not None and field and field["scope"] == CLIENT
            and _panel_value(key, org_id)):
        return "client"
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


def _writable_field(key, org_id=None):
    """The field for ``key``, refusing unknown, structural or wrong-scope writes.

    An ``org_id`` means a client is writing, and a client may only ever write its
    own scope - so it can never reach a platform key such as ``DEEPSEEK_API_KEY``.
    """
    field = FIELDS.get(key)
    if field is None:
        raise SettingsError("unknown setting %r" % key)
    if field.get("readonly"):
        raise SettingsError("%s is structural - edit .env and restart instead" % key)
    if org_id is not None and field["scope"] != CLIENT:
        raise SettingsError("%s configures the platform and is not yours to change" % key)
    return field


def set_value(key, value, actor="", org_id=None):
    """Store one panel value, audit it and drop the cache. Returns the key."""
    field = _writable_field(key, org_id)
    value = _validate(field, value)
    detail = _detail(field, value)
    if org_id is not None:
        detail = "client %s: %s" % (org_id, detail)
    is_secret = 1 if field["secret"] else 0

    if org_id is None:
        table, where, params = "settings", "key = ?", (key,)
        insert = (
            "INSERT INTO settings (key, value, is_secret, updated_at, updated_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, value, is_secret, now_str(), actor),
        )
        update = (
            "UPDATE settings SET value = ?, is_secret = ?, updated_at = ?, "
            "updated_by = ? WHERE key = ?",
            (value, is_secret, now_str(), actor, key),
        )
    else:
        table, where, params = "client_settings", "org_id = ? AND key = ?", (org_id, key)
        insert = (
            "INSERT INTO client_settings (org_id, key, value, is_secret, updated_at, "
            "updated_by) VALUES (?, ?, ?, ?, ?, ?)",
            (org_id, key, value, is_secret, now_str(), actor),
        )
        update = (
            "UPDATE client_settings SET value = ?, is_secret = ?, updated_at = ?, "
            "updated_by = ? WHERE org_id = ? AND key = ?",
            (value, is_secret, now_str(), actor, org_id, key),
        )

    with get_connection() as conn:
        existing = conn.execute(
            "SELECT key FROM %s WHERE %s" % (table, where), params
        ).fetchone()
        conn.execute(*(update if existing else insert))
        conn.execute(
            "INSERT INTO settings_events (key, action, actor, detail) "
            "VALUES (?, ?, ?, ?)",
            (key, "set", actor, detail),
        )
    forget(key)
    return key


def clear_value(key, actor="", org_id=None):
    """Remove a stored value so the next layer (or the default) applies again."""
    _writable_field(key, org_id)
    if org_id is None:
        sql, params = "DELETE FROM settings WHERE key = ?", (key,)
        detail = "cleared; falling back to the environment"
    else:
        sql = "DELETE FROM client_settings WHERE org_id = ? AND key = ?"
        params = (org_id, key)
        detail = "client %s: cleared; falling back to the platform value" % org_id
    with get_connection() as conn:
        conn.execute(sql, params)
        conn.execute(
            "INSERT INTO settings_events (key, action, actor, detail) "
            "VALUES (?, ?, ?, ?)",
            (key, "clear", actor, detail),
        )
    forget(key)
    return key


def update_many(values, clear=(), actor="", org_id=None):
    """Apply a batch. A blank secret means "keep what is stored".

    The form never receives the existing secret, so an empty box has to mean
    "unchanged" - otherwise every save would wipe the key it cannot display.
    """
    changed = []
    for key, value in (values or {}).items():
        field = _writable_field(key, org_id)
        if field["secret"] and not str(value or "").strip():
            continue
        set_value(key, value, actor, org_id)
        changed.append(key)
    for key in clear or ():
        clear_value(key, actor, org_id)
        changed.append(key)
    return changed


# ---------------------------------------------------------------------------
# Admin UI payload
# ---------------------------------------------------------------------------
def _field_for_ui(field, org_id=None):
    """One field as the panel needs it. Secret values are never included."""
    key = field["key"]
    client_value = None
    if org_id is not None and field["scope"] == CLIENT:
        client_value = _panel_value(key, org_id)
    panel = _panel_value(key)
    env = (os.environ.get(key) or "").strip()
    if client_value:
        origin, value = "client", client_value
    elif panel:
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
        "scope": field["scope"],
        "set": bool(value),
        "source": origin,
    }
    if value and field["secret"]:
        # Never ship the value itself - only a hint that it is set.
        out["preview"] = mask(value)
    elif not field["secret"]:
        out["value"] = value
    return out


def all_for_ui(org_id=None):
    """Grouped settings for the panel. Secret values are never included.

    With ``org_id`` set only client-scoped groups are returned - a client's own
    SMTP, and nothing else. That keeps the platform's key names off a client's
    page entirely, rather than merely blanking their values.
    """
    groups = []
    for group in SPEC:
        if org_id is not None and group_scope(group) != CLIENT:
            continue
        groups.append({
            "id": group["id"],
            "label": group["label"],
            "icon": group["icon"],
            "blurb": group["blurb"],
            "test": group.get("test"),
            "scope": group_scope(group),
            "fields": [_field_for_ui(field, org_id) for field in group["fields"]],
        })
    return groups


def history(limit=50, org_id=None):
    """The most recent settings changes, newest first.

    With ``org_id`` set, only that client's changes. The audit table has no org
    column - the org is recorded in the detail prefix - so this filters on that
    rather than widening the table for one query. The pattern is a bound parameter
    so no dialect has to escape it.
    """
    query = "SELECT id, key, action, actor, detail, created_at FROM settings_events "
    params = []
    if org_id is None:
        query += "WHERE detail NOT LIKE ?"
        params.append("client %")
    else:
        query += "WHERE detail LIKE ?"
        params.append("client %s: " % org_id + "%")
    query += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, int(limit)))
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Live checks (the "Test" buttons)
# ---------------------------------------------------------------------------
def _group(group_id):
    for group in SPEC:
        if group["id"] == group_id:
            return group
    return None


def test(group_id, org_id=None):
    """Check one group for real. Returns ``{ok, output}`` and never raises.

    With an ``org_id`` only client-scoped groups may be tested: running the
    platform's Pexels check would spend the operator's key on a client's behalf and
    confirm to them that it is there.
    """
    group = _group(group_id)
    if group is None:
        return {"ok": False, "output": "No test available for %r." % group_id}
    if org_id is not None and group_scope(group) != CLIENT:
        return {"ok": False, "output": "That group configures the platform, not a client."}
    runner = {
        "pexels": _test_pexels,
        "llm": _test_llm,
        "smtp": _test_smtp,
    }.get(group_id)
    if runner is None:
        return {"ok": False, "output": "No test available for %r." % group_id}
    try:
        return runner(org_id)
    except Exception as exc:  # pragma: no cover - defensive
        return {"ok": False, "output": "%s: %s" % (type(exc).__name__, exc)}


def _test_pexels(org_id=None):
    if not get("PEXELS_API_KEY", org_id=org_id):
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


def _test_llm(org_id=None):
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


def _test_smtp(org_id=None):
    host = get("SMTP_HOST", org_id=org_id)
    if not host:
        return {"ok": False, "output": "No SMTP host set."}
    import smtplib

    port = get_int("SMTP_PORT", 587, org_id=org_id)
    user = get("SMTP_USER", org_id=org_id)
    try:
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.ehlo()
            if get_bool("SMTP_TLS", True, org_id=org_id):
                server.starttls()
                server.ehlo()
            if user:
                server.login(user, get("SMTP_PASS", org_id=org_id))
    except Exception as exc:
        return {"ok": False, "output": "%s: %s" % (type(exc).__name__, exc)}
    return {
        "ok": True,
        "output": "Connected to %s:%d%s."
        % (host, port, " and signed in" if user else ""),
    }