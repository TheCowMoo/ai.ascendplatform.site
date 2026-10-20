"""
AscendPlatform — Flask backend (business AI platform).

A multi-bot AI chatbot platform:
  * Admin panel (served from /admin) to create and manage multiple chatbots.
  * Per-bot knowledge base + personality (system prompt).
  * Per-bot analytics (messages, conversations, top questions).
  * Multi-user accounts (admin / member roles).
  * Public embed widget (widget.js) that loads a bot by id.
  * Chat endpoint that queries DeepSeek using the selected bot's knowledge base.

Storage: SQLite (data/app.db). Auth: Flask signed sessions + Werkzeug password hashing.
"""

import os
import json
import re
import secrets
import threading
import datetime
import urllib.request
from functools import wraps

from flask import Flask, jsonify, request, session, redirect, send_from_directory
from flask_cors import CORS
from openai import OpenAI
from werkzeug.security import generate_password_hash, check_password_hash

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from db import get_connection, init_db, new_bot_id, IntegrityError, now_str, days_ago, seed_lock
import meta as meta_api
import llm
import jobs
import profile as profile_mod
import agents as agent_roster
import settings
import orgs as orgs_service
import deliverables as deliverables_service
import analytics as analytics_service
import invites as invites_service
import tools as tool_registry
import actions
import provisioner
import servers as servers_service
import sites as sites_service
from tools import policy as tool_policy
from tools import ssh as ssh_tool

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, static_folder=None)
CORS(app)

# These are read per call rather than captured at import, so a change made in the
# Settings tab applies to the next request without restarting the app.
def deepseek_config():
    """``(api_key, base_url, model)`` for the plain-SDK chat path."""
    return (
        settings.get("DEEPSEEK_API_KEY"),
        settings.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com",
        settings.get("MODEL") or "deepseek-chat",
    )


def max_kb_chars():
    return settings.get_int("MAX_KB_CHARS", 30000)


# Product name (shown in the admin UI and used in outbound copy).
def brand():
    return settings.get("BRAND_NAME") or "AscendPlatform"

# Meta (Messenger / Instagram / WhatsApp) webhook integration.
# A per-channel app secret / verify token (set in the admin panel) overrides
# these globals; set them here if all your channels share one Meta app.
def meta_app_secret():
    return settings.get("META_APP_SECRET")


def meta_verify_token():
    return settings.get("META_VERIFY_TOKEN")


def _load_or_create_secret():
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    path = os.path.join(BASE_DIR, ".secret_key")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    with open(path, "w", encoding="utf-8") as f:
        f.write(key)
    return key


app.secret_key = _load_or_create_secret()

# The DeepSeek client is built on demand and then re-used, so rotating the key in
# the Settings tab takes effect on the next message. Only one client is kept.
_client_cache = {}


def get_client():
    """The DeepSeek client for the widget/Meta chat path, or None when unset."""
    api_key, base_url, _model = deepseek_config()
    if not api_key:
        return None
    cached = _client_cache.get((api_key, base_url))
    if cached is None:
        cached = OpenAI(api_key=api_key, base_url=base_url)
        _client_cache.clear()
        _client_cache[(api_key, base_url)] = cached
    return cached

DEFAULT_SYSTEM_PROMPT = (
    "You are {assistant_name}, a helpful support assistant for {name}. "
    "Answer the user's questions accurately using ONLY the provided knowledge base context. "
    "If the answer cannot be found in the knowledge base, politely say that you do not know "
    "and suggest the user contact support. Do not invent information.\n\n"
    "Knowledge Base:\n{context}"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def as_dict(row):
    return {k: row[k] for k in row.keys()}


def bot_to_dict(row, message_count=None):
    d = {
        "id": row["id"],
        "name": row["name"],
        "assistant_name": row["assistant_name"],
        "brand_color": row["brand_color"],
        "logo_url": row["logo_url"] or "",
        "welcome_message": row["welcome_message"] or "",
        "quick_replies": json.loads(row["quick_replies"] or "[]"),
        "system_prompt": row["system_prompt"] or "",
        "knowledge_base": row["knowledge_base"] or "",
        "webhook_url": row["webhook_url"] or "",
        "profile_id": row["profile_id"],
        "use_profile_kb": bool(row["use_profile_kb"]),
        "owner_id": row["owner_id"],
        "org_id": row["org_id"] if "org_id" in row.keys() else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if message_count is not None:
        d["message_count"] = message_count
    return d


def profile_out(row):
    """Serialise a Business Profile row, parsing its JSON columns for the client."""
    if not row:
        return None
    out = as_dict(row)
    for key in ("services", "faq"):
        try:
            out[key] = json.loads(row[key] or "[]")
        except Exception:
            out[key] = []
    try:
        out["social"] = json.loads(row["social"] or "{}")
    except Exception:
        out["social"] = {}
    return out


def load_legacy_knowledge_base():
    path = os.path.join(BASE_DIR, "knowledge", "cheryl_ai_knowledgebase.txt")
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
    except Exception:
        pass
    return ""


EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
NAME_PATTERNS = [
    r"(?:my name is|name is|i am|i'm|im|this is)\s+([A-Za-z][A-Za-z' -]{1,40})",
]


def _extract_email(text):
    m = EMAIL_RE.search(text or "")
    return m.group(0).lower() if m else None


def _extract_name(text):
    for pat in NAME_PATTERNS:
        m = re.search(pat, text or "", re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return None


def _send_webhook_sync(url, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status


def _send_webhook_async(url, payload):
    def _run():
        try:
            _send_webhook_sync(url, payload)
        except Exception as exc:
            app.logger.error("Webhook send failed: %s", exc)

    threading.Thread(target=_run, daemon=True).start()


WEBHOOK_HINT = (
    "\n\nYou may send data to an external system by including a line in your reply that starts "
    "with 'WEBHOOK_PAYLOAD:' followed by a single-line JSON object. "
    "Example: WEBHOOK_PAYLOAD: {\"name\": \"John\", \"email\": \"john@example.com\"}. "
    "Only do this when the knowledge base instructs you to collect and send specific information."
)


def _parse_json_object(text):
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(text[start:i + 1])
                    return obj if isinstance(obj, dict) else None
                except Exception:
                    return None
    return None


def _extract_webhook_payload(reply):
    lines = (reply or "").split("\n")
    payload = None
    cleaned = []
    for line in lines:
        if "WEBHOOK_PAYLOAD" in line:
            idx = line.find("WEBHOOK_PAYLOAD")
            rest = line[idx + len("WEBHOOK_PAYLOAD"):].lstrip(": ")
            obj = _parse_json_object(rest)
            if obj is not None:
                payload = obj
            continue
        cleaned.append(line)
    return payload, "\n".join(cleaned).strip()


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)

    return wrapper


# Roles. `owner` is the platform operator: they belong to no org and see every
# client's rows. The other two belong to an org and are scoped to it.
OWNER_ROLE = "owner"
CLIENT_ADMIN_ROLE = "client_admin"
MEMBER_ROLE = "member"
ROLES = (OWNER_ROLE, CLIENT_ADMIN_ROLE, MEMBER_ROLE)

# Enforced when a password is set. The seeded first account uses the shorter default
# password so a fresh install is reachable; see README.
MIN_PASSWORD_LEN = 8

# The pre-tenancy name for the platform operator. Migration 0005 renames these rows
# to `owner`; the alias keeps a database that has not been migrated - and every
# existing login - working until it is.
LEGACY_OWNER_ROLE = "admin"


def current_org():
    """The tenant for this request, taken from the signed session only.

    Never read the org from request input: it is the one value an attacker controls,
    and it decides every scoped query.
    """
    return session.get("org_id")


def is_owner_role(role):
    """True for the platform operator. 'admin' is the pre-tenancy spelling."""
    return role in (OWNER_ROLE, LEGACY_OWNER_ROLE)


def is_owner():
    return is_owner_role(session.get("role"))


def owner_required(f):
    """Platform-operator only: clients, servers, the agent roster, platform settings."""

    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"error": "Unauthorized"}), 401
        if not is_owner():
            return jsonify({"error": "Forbidden"}), 403
        return f(*args, **kwargs)

    return wrapper


def client_admin_required(f):
    """A client's own admin - or the platform operator, who can act for anyone."""

    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"error": "Unauthorized"}), 401
        if not is_owner() and session.get("role") != CLIENT_ADMIN_ROLE:
            return jsonify({"error": "Forbidden"}), 403
        return f(*args, **kwargs)

    return wrapper


def can_access_org(row_org_id):
    """True when the caller may touch a row owned by ``row_org_id``.

    The platform owner may touch anything. Everyone else is confined to their own
    org - and a signed-in user with *no* org (a legacy login, or one created without
    one) is confined to nothing, so they can never reach the NULL-org rows that
    predate tenancy. Comparing ``None != None`` would have said "yes" there.
    """
    if is_owner():
        return True
    return current_org() is not None and row_org_id == current_org()


def list_scope():
    """The org id a list endpoint should filter by.

    ``None`` means "every client", and only the platform owner may ask for that. A
    signed-in user with no org gets 0, which no row can have (ids start at 1) -
    passing their ``None`` straight through would hand them the owner's unfiltered
    view, which is the one value that must never leak.
    """
    if is_owner():
        return None
    return current_org() or 0


def org_filter(column="org_id"):
    """``(sql_fragment, params)`` restricting a query to the caller's client.

    Empty for the platform owner, who sees across every client. Uses the same
    0-matches-nothing rule as ``list_scope``.
    """
    if is_owner():
        return "", []
    return " AND %s = ?" % column, [current_org() or 0]


def _check_new_password(password):
    """None when a new password is acceptable, otherwise why it is not.

    Length is the only rule, deliberately: composition rules push people towards
    predictable substitutions, and there is no breach list to check against here.
    """
    if len(password or "") < MIN_PASSWORD_LEN:
        return "The new password must be at least %d characters" % MIN_PASSWORD_LEN
    return None


def profile_org():
    """The Business Profile this request reads or writes.

    The platform's own brand lives at org 0, so it is never mistaken for a client's.
    A login with no org gets None: it belongs to no client, so it has no profile.
    """
    if is_owner():
        return profile_mod.PLATFORM_ORG
    return current_org()


def bot_profile_org(bot):
    """Whose Business Profile a bot inherits: its client's, or the platform's.

    Bots created before tenancy have no org, and those belong to the platform.
    """
    org_id = bot.get("org_id") if hasattr(bot, "get") else None
    return profile_mod.PLATFORM_ORG if org_id is None else org_id


def seed_initial_data():
    try:
        with get_connection() as conn:
            # Two uvicorn workers import this module at once, so without the lock the
            # "count, then insert" below runs twice on a fresh database: two admin
            # users (one fails on the unique email and is swallowed) and two "Cheryl"
            # bots, exactly as the agent roster was duplicated.
            seed_lock(conn)
            n = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            if n == 0:
                email = os.environ.get("ADMIN_EMAIL", "admin@example.com").strip().lower()
                password = os.environ.get("ADMIN_PASSWORD", "admin123")
                name = os.environ.get("ADMIN_NAME", "Admin")
                conn.execute(
                    "INSERT INTO users (email, name, password_hash, role) VALUES (?, ?, ?, 'owner')",
                    (email, name, generate_password_hash(password)),
                )
                app.logger.warning("Seeded admin account %r — change the default password!", email)
            b = conn.execute("SELECT COUNT(*) FROM bots").fetchone()[0]
            if b == 0:
                bot_id = new_bot_id()
                conn.execute(
                    "INSERT INTO bots (id, name, assistant_name, welcome_message, quick_replies, knowledge_base) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        bot_id,
                        "Cheryl",
                        "Cheryl",
                        "Hi! I'm Cheryl, your virtual assistant. How can I help you today?",
                        json.dumps(["What services do you offer?", "What are your prices?", "How do I contact support?"]),
                        load_legacy_knowledge_base(),
                    ),
                )
                app.logger.info("Seeded initial bot %r", bot_id)
    except Exception as exc:  # pragma: no cover - defensive only
        app.logger.error("Seed error (ignored): %s", exc)

    # Seed the platform's own Business Profile (org 0) for the first account.
    try:
        with get_connection() as conn:
            admin = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
        if admin and not profile_mod.get_profile(profile_mod.PLATFORM_ORG):
            profile_mod.seed_sample_profile(profile_mod.PLATFORM_ORG, admin["id"])
            app.logger.info("Seeded the platform Business Profile (org %s)", profile_mod.PLATFORM_ORG)
    except Exception as exc:  # pragma: no cover - defensive only
        app.logger.error("Profile seed error (ignored): %s", exc)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
@app.route("/api/auth/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if row and check_password_hash(row["password_hash"], password):
        session["user_id"] = row["id"]
        session["role"] = row["role"]
        session["name"] = row["name"]
        session["email"] = row["email"]
        # The tenant lives in the signed session, never in request input.
        session["org_id"] = row["org_id"]
        return jsonify({"user": {
            "id": row["id"], "email": row["email"], "name": row["name"],
            "role": row["role"], "org_id": row["org_id"],
            # Sent with the login too, so the nav is correct without a reload.
            "is_owner": is_owner_role(row["role"]),
        }})
    return jsonify({"error": "Invalid email or password"}), 401


@app.route("/api/auth/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/auth/password", methods=["POST"])
@login_required
def change_password():
    """Change your own password. The current one is required, so a stolen session
    alone cannot lock the owner out of their own account."""
    data = request.get_json(silent=True) or {}
    current = data.get("current_password") or ""
    new = data.get("new_password") or ""
    problem = _check_new_password(new)
    if problem:
        return jsonify({"error": problem}), 400

    with get_connection() as conn:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE id = ?", (session["user_id"],)
        ).fetchone()
        if not row or not check_password_hash(row["password_hash"], current):
            return jsonify({"error": "That is not your current password"}), 403
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(new), session["user_id"]),
        )
    app.logger.info("Password changed by %s", session.get("email"))
    return jsonify({"ok": True})


@app.route("/api/users/<int:user_id>/password", methods=["POST"])
@client_admin_required
def reset_user_password(user_id):
    """Set someone else's password - a client admin for their own team.

    No current password is asked for: the caller is an admin acting on their own
    org, and an admin who cannot get in has no way to recover otherwise.
    """
    data = request.get_json(silent=True) or {}
    new = data.get("new_password") or data.get("password") or ""
    problem = _check_new_password(new)
    if problem:
        return jsonify({"error": problem}), 400

    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, role, org_id FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        # Same rule as deleting a user: confined to your own org, never an operator.
        if not is_owner() and (row["role"] not in (CLIENT_ADMIN_ROLE, MEMBER_ROLE)
                               or not can_access_org(row["org_id"])):
            return jsonify({"error": "Forbidden"}), 403
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(new), user_id),
        )
    app.logger.info("Password reset for user %s by %s", user_id, session.get("email"))
    return jsonify({"ok": True})


@app.route("/api/auth/me", methods=["GET"])
def me():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"user": None})
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, email, name, role, org_id FROM users WHERE id = ?", (uid,)
        ).fetchone()
    if not row:
        session.clear()
        return jsonify({"user": None})
    payload = as_dict(row)
    payload["is_owner"] = is_owner_role(row["role"])
    payload["org"] = orgs_service.get_org(row["org_id"]) if row["org_id"] else None
    return jsonify({"user": payload})


# ---------------------------------------------------------------------------
# Users - a client's own team. The platform owner sees every client.
# ---------------------------------------------------------------------------
@app.route("/api/users", methods=["GET"])
@client_admin_required
def list_users():
    clause, params = org_filter()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, email, name, role, org_id, created_at FROM users "
            "WHERE 1 = 1" + clause + " ORDER BY created_at",
            params,
        ).fetchall()
    return jsonify({"users": [as_dict(r) for r in rows]})


@app.route("/api/users", methods=["POST"])
@client_admin_required
def create_user():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    name = (data.get("name") or "").strip() or email
    password = data.get("password") or ""
    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400

    # A client admin can only create client roles, and only inside their own org.
    # The platform owner may create another operator (no org) if they omit org_id.
    allowed = ROLES if is_owner() else (CLIENT_ADMIN_ROLE, MEMBER_ROLE)
    role = data.get("role") or MEMBER_ROLE
    if role not in allowed:
        role = MEMBER_ROLE
    org_id = data.get("org_id") if is_owner() else current_org()

    try:
        with get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO users (email, name, password_hash, role, org_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (email, name, generate_password_hash(password), role, org_id),
            )
            uid = cur.lastrowid
        return jsonify({"user": {
            "id": uid, "email": email, "name": name, "role": role, "org_id": org_id,
        }}), 201
    except Exception as exc:
        return jsonify({"error": "Could not create user (email may already exist): %s" % exc}), 400


@app.route("/api/users/<int:user_id>", methods=["DELETE"])
@client_admin_required
def delete_user(user_id):
    if user_id == session.get("user_id"):
        return jsonify({"error": "You cannot delete your own account"}), 400
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, role, org_id FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        # A client admin may only remove their own org's staff, and never a platform
        # operator. Without this a client admin could delete the owner's account.
        if not is_owner() and (row["role"] not in (CLIENT_ADMIN_ROLE, MEMBER_ROLE)
                               or not can_access_org(row["org_id"])):
            return jsonify({"error": "Forbidden"}), 403
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Bots
# ---------------------------------------------------------------------------
def _bot_for_request(bot_id):
    """Load a bot the caller may act on. 404 for another client's bot, never 403."""
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()
    if not row:
        return None
    bot = bot_to_dict(row)
    if not can_access_org(bot.get("org_id")):
        return None
    return bot


@app.route("/api/bots", methods=["GET"])
@login_required
def list_bots():
    clause, params = org_filter("b.org_id")
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT b.*, COUNT(m.id) AS message_count FROM bots b "
            "LEFT JOIN messages m ON m.bot_id = b.id WHERE 1 = 1" + clause +
            " GROUP BY b.id ORDER BY b.created_at DESC",
            params,
        ).fetchall()
    return jsonify({"bots": [bot_to_dict(r, r["message_count"]) for r in rows]})


@app.route("/api/bots", methods=["POST"])
@login_required
def create_bot():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400
    bot_id = new_bot_id()
    # The bot inherits its client's profile, not one specific user's.
    owner_profile = profile_mod.ensure_profile(profile_org(), session.get("user_id")) or {}
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO bots (id, name, assistant_name, brand_color, logo_url, welcome_message, quick_replies, system_prompt, knowledge_base, webhook_url, profile_id, use_profile_kb, owner_id, org_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                bot_id,
                name,
                (data.get("assistant_name") or "").strip() or "Assistant",
                # Blank brand colour means "inherit from the Business Profile".
                (data.get("brand_color") or "").strip(),
                (data.get("logo_url") or "").strip(),
                (data.get("welcome_message") or "").strip(),
                json.dumps(data.get("quick_replies") or []),
                (data.get("system_prompt") or "").strip(),
                data.get("knowledge_base") or "",
                (data.get("webhook_url") or "").strip(),
                owner_profile.get("id"),
                1 if data.get("use_profile_kb", True) else 0,
                session.get("user_id"),
                current_org(),
            ),
        )
        row = conn.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()
    return jsonify({"bot": bot_to_dict(row)}), 201


@app.route("/api/bots/<bot_id>", methods=["GET"])
@login_required
def get_bot(bot_id):
    bot = _bot_for_request(bot_id)
    if not bot:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"bot": bot})


@app.route("/api/bots/<bot_id>", methods=["PUT"])
@client_admin_required
def update_bot(bot_id):
    if not _bot_for_request(bot_id):
        return jsonify({"error": "Not found"}), 404
    data = request.get_json(silent=True) or {}
    with get_connection() as conn:
        existing = conn.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()
        if not existing:
            return jsonify({"error": "Not found"}), 404
        conn.execute(
            "UPDATE bots SET name=?, assistant_name=?, brand_color=?, logo_url=?, welcome_message=?, quick_replies=?, system_prompt=?, knowledge_base=?, webhook_url=?, updated_at=? WHERE id=?",
            (
                (data.get("name") or existing["name"]).strip(),
                (data.get("assistant_name") or existing["assistant_name"]).strip(),
                (data.get("brand_color") or existing["brand_color"]).strip(),
                (data.get("logo_url") or existing["logo_url"]).strip(),
                (data.get("welcome_message") or existing["welcome_message"]).strip(),
                json.dumps(data.get("quick_replies", json.loads(existing["quick_replies"] or "[]"))),
                (data.get("system_prompt") or existing["system_prompt"]).strip(),
                data.get("knowledge_base", existing["knowledge_base"]),
                (data.get("webhook_url") or existing["webhook_url"] or "").strip(),
                now_str(),
                bot_id,
            ),
        )
        row = conn.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()
    return jsonify({"bot": bot_to_dict(row)})


@app.route("/api/bots/<bot_id>", methods=["DELETE"])
@client_admin_required
def delete_bot(bot_id):
    if not _bot_for_request(bot_id):
        return jsonify({"error": "Not found"}), 404
    with get_connection() as conn:
        conn.execute("DELETE FROM messages WHERE bot_id = ?", (bot_id,))
        conn.execute("DELETE FROM channels WHERE bot_id = ?", (bot_id,))
        conn.execute("DELETE FROM bots WHERE id = ?", (bot_id,))
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Channels (Meta: Messenger / Instagram / WhatsApp)
# ---------------------------------------------------------------------------
CHANNEL_PLATFORMS = ("messenger", "instagram", "whatsapp")


def channel_to_dict(row, mask=True):
    """Serialise a channel row. Tokens/secrets are hidden unless mask=False."""
    d = {
        "id": row["id"],
        "bot_id": row["bot_id"],
        "platform": row["platform"],
        "external_id": row["external_id"],
        "enabled": bool(row["enabled"]),
        "verify_token": row["verify_token"] or "",
        "has_token": bool(row["access_token"]),
        "has_app_secret": bool(row["app_secret"]),
        "created_at": row["created_at"],
    }
    if not mask:
        d["access_token"] = row["access_token"] or ""
        d["app_secret"] = row["app_secret"] or ""
    return d


@app.route("/api/bots/<bot_id>/channels", methods=["GET"])
@login_required
def list_channels(bot_id):
    if not _bot_for_request(bot_id):
        return jsonify({"error": "Not found"}), 404
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM channels WHERE bot_id = ? ORDER BY created_at", (bot_id,)).fetchall()
    return jsonify({"channels": [channel_to_dict(r) for r in rows]})


@app.route("/api/bots/<bot_id>/channels", methods=["POST"])
@login_required
def create_channel(bot_id):
    data = request.get_json(silent=True) or {}
    platform = (data.get("platform") or "").strip().lower()
    external_id = (data.get("external_id") or "").strip()
    if platform not in CHANNEL_PLATFORMS:
        return jsonify({"error": "Invalid platform"}), 400
    if not external_id:
        return jsonify({"error": "Page/Instagram/Phone ID is required"}), 400
    verify_token = (data.get("verify_token") or "").strip() or secrets.token_urlsafe(16)
    if not _bot_for_request(bot_id):
        return jsonify({"error": "Not found"}), 404
    with get_connection() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO channels (bot_id, platform, external_id, access_token, app_secret, verify_token) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    bot_id,
                    platform,
                    external_id,
                    (data.get("access_token") or "").strip(),
                    (data.get("app_secret") or "").strip(),
                    verify_token,
                ),
            )
        except IntegrityError:
            return jsonify({"error": "That platform + ID is already linked"}), 409
        row = conn.execute("SELECT * FROM channels WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify({"channel": channel_to_dict(row)}), 201


@app.route("/api/bots/<bot_id>/channels/<int:channel_id>", methods=["PUT"])
@client_admin_required
def update_channel(bot_id, channel_id):
    if not _bot_for_request(bot_id):
        return jsonify({"error": "Not found"}), 404
    data = request.get_json(silent=True) or {}
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT * FROM channels WHERE id = ? AND bot_id = ?", (channel_id, bot_id)
        ).fetchone()
        if not existing:
            return jsonify({"error": "Not found"}), 404
        platform = (data.get("platform") or existing["platform"]).strip().lower()
        if platform not in CHANNEL_PLATFORMS:
            return jsonify({"error": "Invalid platform"}), 400
        # Blank token/secret fields keep their stored value (so the UI can mask them).
        access_token = (data.get("access_token") or "").strip() or existing["access_token"]
        app_secret = (data.get("app_secret") or "").strip() or existing["app_secret"]
        try:
            conn.execute(
                "UPDATE channels SET platform=?, external_id=?, access_token=?, app_secret=?, verify_token=?, enabled=? "
                "WHERE id=?",
                (
                    platform,
                    (data.get("external_id") or existing["external_id"]).strip(),
                    access_token,
                    app_secret,
                    (data.get("verify_token") or existing["verify_token"]).strip(),
                    1 if data.get("enabled", bool(existing["enabled"])) else 0,
                    channel_id,
                ),
            )
        except IntegrityError:
            return jsonify({"error": "That platform + ID is already linked"}), 409
        row = conn.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
    return jsonify({"channel": channel_to_dict(row)})


@app.route("/api/bots/<bot_id>/channels/<int:channel_id>", methods=["DELETE"])
@client_admin_required
def delete_channel(bot_id, channel_id):
    if not _bot_for_request(bot_id):
        return jsonify({"error": "Not found"}), 404
    with get_connection() as conn:
        conn.execute("DELETE FROM channels WHERE id = ? AND bot_id = ?", (channel_id, bot_id))
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
@app.route("/api/bots/<bot_id>/stats", methods=["GET"])
@login_required
def bot_stats(bot_id):
    if not _bot_for_request(bot_id):
        return jsonify({"error": "Not found"}), 404
    with get_connection() as conn:
        total_messages = conn.execute("SELECT COUNT(*) FROM messages WHERE bot_id = ?", (bot_id,)).fetchone()[0]
        total_conversations = conn.execute("SELECT COUNT(DISTINCT conversation_id) FROM messages WHERE bot_id = ?", (bot_id,)).fetchone()[0]
        # Portable (SQLite + PostgreSQL): timestamps are TEXT in 'YYYY-MM-DD HH:MM:SS',
        # compared lexicographically and grouped by their date prefix.
        per_day = conn.execute(
            "SELECT substr(created_at, 1, 10) AS d, COUNT(*) AS c FROM messages "
            "WHERE bot_id = ? AND created_at >= ? GROUP BY substr(created_at, 1, 10) ORDER BY d",
            (bot_id, days_ago(14)),
        ).fetchall()
        top = conn.execute(
            "SELECT MIN(content) AS content, COUNT(*) AS c FROM messages "
            "WHERE bot_id = ? AND role = 'user' GROUP BY lower(trim(content)) ORDER BY c DESC LIMIT 10",
            (bot_id,),
        ).fetchall()
    return jsonify({
        "total_messages": total_messages,
        "total_conversations": total_conversations,
        "messages_per_day": [{"date": r["d"], "count": r["c"]} for r in per_day],
        "top_questions": [{"question": r["content"], "count": r["c"]} for r in top],
    })


# ---------------------------------------------------------------------------
# Conversations (for follow-up)
# ---------------------------------------------------------------------------
@app.route("/api/bots/<bot_id>/conversations", methods=["GET"])
@login_required
def list_conversations(bot_id):
    bot = _bot_for_request(bot_id)
    if not bot:
        return jsonify({"error": "Not found"}), 404
    with get_connection() as conn:
        msgs = conn.execute(
            "SELECT conversation_id, role, content, created_at FROM messages WHERE bot_id = ? ORDER BY created_at ASC",
            (bot_id,),
        ).fetchall()

    convs = {}
    for m in msgs:
        cid = m["conversation_id"]
        c = convs.get(cid)
        if c is None:
            c = {"conversation_id": cid, "first_at": m["created_at"], "last_at": m["created_at"], "message_count": 0, "user_msgs": []}
            convs[cid] = c
        c["last_at"] = m["created_at"]
        c["message_count"] += 1
        if m["role"] == "user":
            c["user_msgs"].append(m["content"])

    result = []
    for cid, c in convs.items():
        email = name = None
        for content in c["user_msgs"]:
            if not email:
                email = _extract_email(content)
            if not name:
                name = _extract_name(content)
            if email and name:
                break
        preview = c["user_msgs"][-1] if c["user_msgs"] else ""
        result.append({
            "conversation_id": cid,
            "first_at": c["first_at"],
            "last_at": c["last_at"],
            "message_count": c["message_count"],
            "preview": preview[:160],
            "lead": {"email": email, "name": name} if email else None,
        })

    result.sort(key=lambda x: x["last_at"], reverse=True)
    return jsonify({"conversations": result, "bot": {"id": bot["id"], "name": bot["name"]}})


@app.route("/api/bots/<bot_id>/conversations/<conv_id>", methods=["GET"])
@login_required
def get_conversation(bot_id, conv_id):
    if not _bot_for_request(bot_id):
        return jsonify({"error": "Not found"}), 404
    with get_connection() as conn:
        msgs = conn.execute(
            "SELECT role, content, created_at FROM messages WHERE bot_id = ? AND conversation_id = ? ORDER BY created_at ASC",
            (bot_id, conv_id),
        ).fetchall()
    if not msgs:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"messages": [as_dict(m) for m in msgs]})


# ---------------------------------------------------------------------------
# Public endpoints (used by the widget)
# ---------------------------------------------------------------------------
@app.route("/api/bots/<bot_id>/public", methods=["GET"])
def bot_public(bot_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    # Branding falls back to the client's Business Profile, so the widget inherits
    # the business identity without per-bot configuration.
    prof = profile_mod.get_profile(bot_profile_org(row))
    fallback_color = (prof or {}).get("brand_color") or "#4f46e5"
    return jsonify({
        "id": row["id"],
        "name": row["name"],
        "assistant_name": row["assistant_name"],
        "brand_color": (row["brand_color"] or "").strip() or fallback_color,
        "logo_url": (row["logo_url"] or "") or ((prof or {}).get("logo_url") or ""),
        "welcome_message": row["welcome_message"] or "",
        "quick_replies": json.loads(row["quick_replies"] or "[]"),
    })


def load_history(bot_id, conversation_id, limit=10):
    """Rebuild recent turns from the messages table.

    Meta channels don't send client-side history with each message, so we
    reconstruct it from what we've already stored for the conversation.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE bot_id = ? AND conversation_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (bot_id, conversation_id, limit),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def generate_reply(bot, user_message, history, conversation_id):
    """Run the RAG chat pipeline for one user turn (shared by all channels).

    Persists the exchange, fires the optional outbound webhook, and returns
    ``(reply, conversation_id, webhook_payload)``. Raises on model errors.
    """
    context = (bot["knowledge_base"] or "").strip()

    # Business Profile knowledge base — inherited by default (bots can opt out).
    business = None
    if bot.get("use_profile_kb", True):
        business = profile_mod.get_profile(bot_profile_org(bot))
        profile_kb = profile_mod.build_knowledge(business)
        if profile_kb:
            context = (profile_kb + "\n\n" + context).strip()

    limit = max_kb_chars()
    if len(context) > limit:
        context = context[:limit]

    if bot["system_prompt"]:
        system_content = bot["system_prompt"] + "\n\nKnowledge Base:\n" + context
    else:
        system_content = DEFAULT_SYSTEM_PROMPT.format(
            assistant_name=bot["assistant_name"] or "Assistant",
            name=bot["name"],
            context=context or "No additional context provided.",
        )
        tone = ((business or {}).get("tone_of_voice") or "").strip()
        if tone:
            system_content += "\n\nBrand voice: " + tone

    if bot.get("webhook_url"):
        system_content += WEBHOOK_HINT

    messages = [{"role": "system", "content": system_content}]
    for msg in (history or [])[-10:]:
        role = msg.get("role")
        content = msg.get("content")
        if role in ("user", "assistant") and isinstance(content, str):
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})

    _api_key, _base_url, model_name = deepseek_config()
    chat_client = get_client()
    if chat_client is None:
        raise RuntimeError("DeepSeek API key is not configured")
    resp = chat_client.chat.completions.create(
        model=model_name, messages=messages, temperature=0.2, max_tokens=800
    )
    webhook_payload, reply = _extract_webhook_payload(resp.choices[0].message.content)
    if not reply:
        reply = "Got it — thanks!"

    with get_connection() as conn:
        conn.execute("INSERT INTO messages (bot_id, conversation_id, role, content) VALUES (?, ?, ?, ?)", (bot["id"], conversation_id, "user", user_message))
        conn.execute("INSERT INTO messages (bot_id, conversation_id, role, content) VALUES (?, ?, ?, ?)", (bot["id"], conversation_id, "assistant", reply))

    # Webhook: forward the AI-constructed payload (emitted as WEBHOOK_PAYLOAD in its reply).
    if webhook_payload is not None and bot.get("webhook_url"):
        _send_webhook_async(bot["webhook_url"], webhook_payload)

    return reply, conversation_id, webhook_payload


@app.route("/api/chat", methods=["POST"])
def chat():
    if get_client() is None:
        return jsonify({"reply": "Server is not configured with a DeepSeek API key."}), 500
    data = request.get_json(silent=True) or {}
    bot_id = data.get("bot_id")
    user_message = (data.get("message") or "").strip()
    conversation_id = data.get("conversation_id") or secrets.token_urlsafe(8)
    history = data.get("history") or []

    if not user_message:
        return jsonify({"reply": "Please send a message."}), 400

    with get_connection() as conn:
        row = conn.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()
    if not row:
        return jsonify({"reply": "Chatbot not found."}), 404

    bot = bot_to_dict(row)
    try:
        reply, conversation_id, _payload = generate_reply(bot, user_message, history, conversation_id)
    except Exception as exc:
        app.logger.error("DeepSeek API error: %s", exc)
        return jsonify({"reply": f"Error connecting to AI: {exc}"}), 500

    return jsonify({"reply": reply, "conversation_id": conversation_id})


# ---------------------------------------------------------------------------
# Meta webhooks (Messenger / Instagram / WhatsApp)
# ---------------------------------------------------------------------------
def _find_channel(platform, external_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM channels WHERE platform = ? AND external_id = ? AND enabled = 1",
            (platform, external_id),
        ).fetchone()
    return as_dict(row) if row else None


def _find_channel_by_verify_token(token):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM channels WHERE verify_token = ? LIMIT 1", (token,)).fetchone()
    return as_dict(row) if row else None


def _process_meta_event(channel, event):
    """Background worker: build a reply for one inbound message and send it back."""
    with get_connection() as conn:
        bot_row = conn.execute("SELECT * FROM bots WHERE id = ?", (channel["bot_id"],)).fetchone()
    if not bot_row:
        app.logger.warning("Meta event for unknown bot %s", channel["bot_id"])
        return
    if get_client() is None:
        app.logger.error("Meta event dropped: DeepSeek API key is not configured")
        return

    bot = bot_to_dict(bot_row)
    conversation_id = "meta:%s:%s:%s" % (event["platform"], event["external_id"], event["sender_id"])
    history = load_history(bot["id"], conversation_id, limit=10)
    try:
        reply, _cid, _payload = generate_reply(bot, event["text"], history, conversation_id)
    except Exception as exc:
        app.logger.error("Meta reply generation failed: %s", exc)
        return
    try:
        status, body = meta_api.send_reply(channel, event["sender_id"], reply)
        if status >= 400:
            app.logger.error("Meta send failed (%s): %s", status, body)
    except Exception as exc:
        app.logger.error("Meta send error: %s", exc)


@app.route("/webhooks/meta", methods=["GET"])
def meta_verify():
    """Meta webhook verification handshake (returns hub.challenge on success)."""
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token") or ""
    challenge = request.args.get("hub.challenge", "")
    channel = _find_channel_by_verify_token(token) if token else None
    if mode == "subscribe" and token and (token == meta_verify_token() or channel is not None):
        return app.response_class(challenge, mimetype="text/plain")
    return jsonify({"error": "Verification failed"}), 403


@app.route("/webhooks/meta", methods=["POST"])
def meta_webhook():
    raw = request.get_data()
    payload = request.get_json(silent=True) or {}
    events = meta_api.parse_events(payload)

    # Nothing actionable (e.g. delivery/read receipts) — acknowledge quietly.
    if not events:
        return app.response_class("EVENT_RECEIVED", mimetype="text/plain")

    # Prefer the global app secret; otherwise fall back to the linked channel's.
    secret = meta_app_secret()
    if not secret:
        first = _find_channel(events[0]["platform"], events[0]["external_id"])
        if first:
            secret = first.get("app_secret") or ""
    if not secret:
        app.logger.error("Meta webhook rejected: no app secret configured")
        return jsonify({"error": "App secret not configured"}), 403
    if not meta_api.verify_signature(raw, request.headers.get("X-Hub-Signature-256", ""), secret):
        app.logger.warning("Meta webhook signature verification failed")
        return jsonify({"error": "Invalid signature"}), 403

    for event in events:
        channel = _find_channel(event["platform"], event["external_id"])
        if not channel:
            app.logger.info("Meta event for unlinked channel %s/%s", event["platform"], event["external_id"])
            continue
        mid = event.get("mid")
        if mid:
            with get_connection() as conn:
                cur = conn.execute("INSERT OR IGNORE INTO meta_events (mid) VALUES (?)", (mid,))
            if cur.rowcount == 0:
                continue  # already handled (Meta redelivery)
        threading.Thread(target=_process_meta_event, args=(channel, event), daemon=True).start()

    return app.response_class("EVENT_RECEIVED", mimetype="text/plain")


# ---------------------------------------------------------------------------
# Business Profile (the AI's knowledge base + brand identity)
#
# One profile per client: the brand belongs to the business, so every login at a
# client shares it. The platform's own profile is org 0.
# ---------------------------------------------------------------------------
def _profile_scope_or_403():
    """The org to act on, or a 403 response for a login attached to no client."""
    org = profile_org()
    if org is None:
        return None, (jsonify({
            "error": "This login is not attached to a client, so it has no profile",
        }), 403)
    return org, None


@app.route("/api/profile", methods=["GET"])
@login_required
def get_business_profile():
    org, error = _profile_scope_or_403()
    if error:
        return error
    prof = profile_mod.ensure_profile(org, session.get("user_id"))
    return jsonify({"profile": profile_out(prof)})


@app.route("/api/profile", methods=["PUT"])
@client_admin_required
def update_business_profile():
    org, error = _profile_scope_or_403()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    prof = profile_mod.update_profile(org, data, session.get("user_id"))
    return jsonify({"profile": profile_out(prof)})


@app.route("/api/profile/knowledge", methods=["GET"])
@login_required
def business_profile_knowledge():
    """The exact text the AI receives as its knowledge base (for preview)."""
    org, error = _profile_scope_or_403()
    if error:
        return error
    prof = profile_mod.get_profile(org)
    text = profile_mod.build_knowledge(prof)
    return jsonify({"knowledge": text, "chars": len(text)})


@app.route("/api/profile/seed-sample", methods=["POST"])
@client_admin_required
def business_profile_seed():
    org, error = _profile_scope_or_403()
    if error:
        return error
    prof = profile_mod.seed_sample_profile(org, session.get("user_id"))
    return jsonify({"profile": profile_out(prof)})


# ---------------------------------------------------------------------------
# Clients (the platform console's onboarding surface)
#
# Operator only, and the HTTP shape of ``orgs.py``: this is where a client is
# created, given its first login, moved between plans and suspended. `create_org`
# seeds the first client_admin in the same call, so a client is never created
# unreachable - which is the failure an onboarding screen invites.
# ---------------------------------------------------------------------------
@app.route("/api/orgs", methods=["GET"])
@owner_required
def list_orgs():
    return jsonify({"orgs": orgs_service.list_orgs()})


@app.route("/api/orgs", methods=["POST"])
@owner_required
def create_org():
    try:
        org = orgs_service.create_org(request.get_json(silent=True) or {})
    except orgs_service.OrgError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"org": org}), 201


@app.route("/api/orgs/<int:org_id>", methods=["GET"])
@owner_required
def get_org(org_id):
    org = orgs_service.get_org(org_id)
    if not org:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"org": org, "users": orgs_service.list_org_users(org_id)})


@app.route("/api/orgs/<int:org_id>", methods=["PUT"])
@owner_required
def update_org(org_id):
    try:
        org = orgs_service.update_org(org_id, request.get_json(silent=True) or {})
    except orgs_service.OrgError as exc:
        return jsonify({"error": str(exc)}), 400
    if not org:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"org": org})


@app.route("/api/orgs/<int:org_id>", methods=["DELETE"])
@owner_required
def delete_org(org_id):
    try:
        removed = orgs_service.delete_org(org_id)
    except orgs_service.OrgError as exc:
        # 409, not 400: the request is valid, the client just is not empty yet.
        return jsonify({"error": str(exc)}), 409
    if not removed:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/orgs/<int:org_id>/users", methods=["POST"])
@owner_required
def add_org_user(org_id):
    try:
        users = orgs_service.add_org_user(org_id, request.get_json(silent=True) or {})
    except orgs_service.OrgError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"users": users}), 201


# ---------------------------------------------------------------------------
# Deliverables (the content a client publishes)
#
# Blogs, funnels and emails. The Library tab is the same list without a kind
# filter, because "everything the team produced" is exactly their union.
# ---------------------------------------------------------------------------
def _deliverable_for_request(deliverable_id):
    """A deliverable the caller may act on, or None. 404 for another client's."""
    item = deliverables_service.get_deliverable(deliverable_id)
    if not item or not can_access_org(item.get("org_id")):
        return None
    return item


@app.route("/api/deliverables", methods=["GET"])
@login_required
def list_deliverables():
    kind = (request.args.get("kind") or "").strip().lower() or None
    if kind and kind not in deliverables_service.KINDS:
        return jsonify({"error": "kind must be one of: %s"
                                 % ", ".join(deliverables_service.KINDS)}), 400
    scope = list_scope()
    return jsonify({
        "deliverables": deliverables_service.list_deliverables(kind, scope),
        "counts": deliverables_service.counts(scope),
        "kinds": list(deliverables_service.KINDS),
        "statuses": list(deliverables_service.STATUSES),
    })


@app.route("/api/deliverables", methods=["POST"])
@client_admin_required
def create_deliverable():
    try:
        item = deliverables_service.create_deliverable(
            request.get_json(silent=True) or {},
            org_id=current_org(),
            created_by=session.get("user_id"),
        )
    except deliverables_service.DeliverableError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"deliverable": item}), 201


@app.route("/api/deliverables/<int:deliverable_id>", methods=["GET"])
@login_required
def get_deliverable(deliverable_id):
    item = _deliverable_for_request(deliverable_id)
    if not item:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"deliverable": item})


@app.route("/api/deliverables/<int:deliverable_id>", methods=["PUT"])
@client_admin_required
def update_deliverable(deliverable_id):
    if not _deliverable_for_request(deliverable_id):
        return jsonify({"error": "Not found"}), 404
    try:
        item = deliverables_service.update_deliverable(
            deliverable_id, request.get_json(silent=True) or {}
        )
    except deliverables_service.DeliverableError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"deliverable": item})


@app.route("/api/deliverables/<int:deliverable_id>", methods=["DELETE"])
@client_admin_required
def delete_deliverable(deliverable_id):
    if not _deliverable_for_request(deliverable_id):
        return jsonify({"error": "Not found"}), 404
    deliverables_service.delete_deliverable(deliverable_id)
    return jsonify({"ok": True})


@app.route("/api/deliverables/<int:deliverable_id>/publish", methods=["POST"])
@client_admin_required
def publish_deliverable(deliverable_id):
    """Push a website page onto one of this client's sites.

    The write itself goes through ``provisioner``'s target, so it hits the same policy
    check and approval table as an agent-initiated write - the difference is only that
    this request records the decision instead of waiting for it.
    """
    if not _deliverable_for_request(deliverable_id):
        return jsonify({"error": "Not found"}), 404
    data = request.get_json(silent=True) or {}
    try:
        result = deliverables_service.publish_deliverable(
            deliverable_id,
            site_id=data.get("site_id"),
            actor=session.get("email") or session.get("name") or "operator",
        )
    except deliverables_service.DeliverableError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


# ---------------------------------------------------------------------------
# Platform email (GoHighLevel)
#
# Operator-only. The GHL credentials are ordinary platform settings, read and written
# through /api/settings like every other deployment value; these routes cover what is
# not a scalar: senders, the custom-field catalogue, and a real test send.
# ---------------------------------------------------------------------------
@app.route("/api/platform/email", methods=["GET"])
@owner_required
def platform_email_status():
    import platform_mail

    fields = []
    error = ""
    if platform_mail.is_configured():
        try:
            fields = platform_mail.custom_fields()
        except platform_mail.MailError as exc:
            error = str(exc)
    return jsonify({
        "configured": platform_mail.is_configured(),
        "senders": platform_mail.senders(),
        "custom_fields": fields,
        "error": error,
        "invites": invites_service.list_invites(limit=50),
        "roles": list(invites_service.ROLES),
        "template": {
            "subject": settings.get("INVITE_SUBJECT"),
            "body": settings.get("INVITE_BODY_HTML") or invites_service.DEFAULT_BODY,
            "default_body": invites_service.DEFAULT_BODY,
            "expires_days": settings.get_int("INVITE_EXPIRES_DAYS", 7),
        },
        # The editor builds its chips and its preview from this, so the tokens it
        # advertises are exactly the ones render() fills.
        "tokens": [
            {"key": key, "label": label, "sample": sample}
            for key, label, sample in invites_service.TOKENS
        ],
    })


@app.route("/api/platform/email/test", methods=["POST"])
@owner_required
def platform_email_test():
    import platform_mail

    return jsonify(platform_mail.test_connection())


@app.route("/api/platform/email/senders", methods=["POST"])
@owner_required
def platform_email_save_sender():
    import platform_mail

    data = request.get_json(silent=True) or {}
    try:
        sender = platform_mail.save_sender(data, sender_id=data.get("id"))
    except platform_mail.MailError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"sender": sender}), 200 if data.get("id") else 201


@app.route("/api/platform/email/senders/<int:sender_id>", methods=["DELETE"])
@owner_required
def platform_email_delete_sender(sender_id):
    import platform_mail

    platform_mail.delete_sender(sender_id)
    return jsonify({"ok": True})


@app.route("/api/platform/email/send-test", methods=["POST"])
@owner_required
def platform_email_send_test():
    """Send a real email: the only reliable proof that a from-address is verified."""
    import platform_mail

    data = request.get_json(silent=True) or {}
    to = (data.get("to") or session.get("email") or "").strip()
    if not to:
        return jsonify({"error": "Give a recipient, or set your own account email"}), 400
    try:
        result = platform_mail.send(
            to,
            "AscendPlatform test email",
            "<p>This is a test from the platform's GoHighLevel sender.</p>"
            "<p>If you can read it, invitations will deliver.</p>",
        )
    except platform_mail.MailError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502
    return jsonify({"ok": True, "delivery": result})


# ---------------------------------------------------------------------------
# Invitations
#
# The link is password-equivalent, so it is emailed, single-use and expiring. Creating
# one also creates the (locked) account, which is why a failed send still leaves a
# usable invitation the operator can resend or hand over by copying the link.
# ---------------------------------------------------------------------------
def _invite_link(token):
    # /admin/ explicitly: "/" only redirects there, and relying on a redirect to carry
    # a fragment is asking for a broken link in some mail clients.
    return "%s/admin/#/accept/%s" % ((request.url_root or "").rstrip("/"), token)


@app.route("/api/invites", methods=["GET"])
@owner_required
def list_invites():
    return jsonify({"invites": invites_service.list_invites()})


@app.route("/api/invites", methods=["POST"])
@owner_required
def create_invite():
    import platform_mail

    data = request.get_json(silent=True) or {}
    org_id = data.get("org_id")
    try:
        org_id = int(org_id) if org_id not in (None, "") else None
    except (TypeError, ValueError):
        return jsonify({"error": "org_id must be a number"}), 400
    try:
        invite, token = invites_service.create(
            data.get("email"),
            name=data.get("name") or "",
            org_id=org_id,
            role=data.get("role") or "client_admin",
            invited_by=session.get("user_id"),
        )
    except invites_service.InviteError as exc:
        return jsonify({"error": str(exc)}), 400

    subject, html = invites_service.render(
        invite, token, base_url=request.url_root, inviter=session.get("name") or ""
    )
    try:
        delivery = platform_mail.send(
            invite["email"], subject, html, custom_fields={"name": invite["name"]}
        )
    except platform_mail.MailError as exc:
        # The invitation exists and its link works - only the mail failed. Return the
        # invite anyway so the console can offer Resend or a copyable link.
        return jsonify({"invite": invite, "link": _invite_link(token), "error": str(exc)}), 502
    return jsonify({"invite": invite, "link": _invite_link(token), "delivery": delivery}), 201


@app.route("/api/invites/<int:invite_id>/resend", methods=["POST"])
@owner_required
def resend_invite(invite_id):
    import platform_mail

    try:
        invite, token = invites_service.resend(invite_id)
    except invites_service.InviteError as exc:
        return jsonify({"error": str(exc)}), 400
    subject, html = invites_service.render(
        invite, token, base_url=request.url_root, inviter=session.get("name") or ""
    )
    try:
        delivery = platform_mail.send(invite["email"], subject, html)
    except platform_mail.MailError as exc:
        return jsonify({"invite": invite, "link": _invite_link(token), "error": str(exc)}), 502
    return jsonify({"invite": invite, "link": _invite_link(token), "delivery": delivery})


@app.route("/api/invites/<int:invite_id>/revoke", methods=["POST"])
@owner_required
def revoke_invite(invite_id):
    return jsonify({"invite": invites_service.revoke(invite_id)})


# Public: the accept page has no session yet. The token is 256 bits of entropy, so the
# endpoint leaks nothing a bearer of the link does not already have.
@app.route("/api/invites/<token>", methods=["GET"])
def preview_invite(token):
    preview = invites_service.preview(token)
    if not preview:
        return jsonify({"error": "That invitation link is not valid."}), 404
    return jsonify({"invite": preview})


@app.route("/api/invites/accept", methods=["POST"])
def accept_invite():
    """Set the password and start the session - the whole point of the link."""
    data = request.get_json(silent=True) or {}
    password = data.get("password") or ""
    problem = _check_new_password(password)
    if problem:
        return jsonify({"error": problem}), 400
    try:
        user = invites_service.accept(data.get("token"), password)
    except invites_service.InviteError as exc:
        return jsonify({"error": str(exc)}), 400

    session.clear()
    session["user_id"] = user["id"]
    session["role"] = user["role"]
    session["name"] = user["name"]
    session["email"] = user["email"]
    session["org_id"] = user["org_id"]
    return jsonify({
        "ok": True,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "name": user["name"],
            "role": user["role"],
            "is_owner": False,
        },
    })


# ---------------------------------------------------------------------------
# Settings (operator-managed configuration)
#
# Two scopes. Platform fields (model keys, Pexels, the SSH limits) configure the
# deployment, so only the operator sees them. Client fields (SMTP) belong to one
# client and resolve per org, so a client admin configures their own mail.
#
# GET never returns a secret value: only whether it is set, where the effective
# value came from, and a short tail so keys can be told apart.
# ---------------------------------------------------------------------------
def settings_org(data=None):
    """The client bucket this settings request works in, or None for the platform.

    A client is always pinned to their own org - never read from request input. The
    operator defaults to the platform scope and may name a client with ``?org_id=N``
    (or in the body) to set that client up during onboarding.
    """
    if not is_owner():
        return current_org()
    raw = request.args.get("org_id")
    if not raw and data:
        raw = data.get("org_id")
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _settings_scope_or_403(data=None):
    """``(org, error)``. An unattached client login has no settings to show."""
    org = settings_org(data)
    if org is None and not is_owner():
        return None, (jsonify({"error": "This login is not attached to a client"}), 403)
    return org, None


@app.route("/api/settings", methods=["GET"])
@client_admin_required
def get_settings():
    org, error = _settings_scope_or_403()
    if error:
        return error
    payload = {
        "groups": settings.all_for_ui(org),
        "history": settings.history(25, org),
        "scoped_to": org,
        "dry_run": actions.is_dry_run(),
    }
    if is_owner():
        # The console needs the client list to edit a client's own settings.
        payload["orgs"] = orgs_service.list_orgs()
    return jsonify(payload)


@app.route("/api/settings", methods=["PUT"])
@client_admin_required
def update_settings():
    """Save a batch. A blank secret means "leave the stored value alone"."""
    data = request.get_json(silent=True) or {}
    org, error = _settings_scope_or_403(data)
    if error:
        return error
    values = data.get("values") or {}
    clear = data.get("clear") or []
    if not isinstance(values, dict) or not isinstance(clear, (list, tuple)):
        return jsonify({"error": "values must be an object and clear a list"}), 400

    actor = session.get("email") or str(session.get("user_id") or "admin")
    if org is not None:
        actor = "%s (for client %s)" % (actor, org)
    try:
        changed = settings.update_many(values, clear, actor, org)
    except settings.SettingsError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        # An unexpected write failure must not reach the browser as a blank 500: the
        # toast shows this text, which is how the PostgreSQL "RETURNING id" bug would
        # have been identified in seconds instead of a round of guesswork.
        app.logger.exception("Settings update failed for %s", actor)
        return jsonify({"error": "Could not save (%s: %s)" % (type(exc).__name__, exc)}), 500
    app.logger.info(
        "Settings updated by %s: %s", actor, ", ".join(changed) or "(nothing changed)"
    )
    return jsonify({"changed": changed, "groups": settings.all_for_ui(org)})


@app.route("/api/settings/test/<group_id>", methods=["POST"])
@client_admin_required
def test_settings_group(group_id):
    """Run a group's live check - e.g. a real Pexels search with the saved key."""
    org, error = _settings_scope_or_403(request.get_json(silent=True) or {})
    if error:
        return error
    return jsonify(settings.test(group_id, org))


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Agents (designatable roster)
#
# Platform-operator only: the roster is the product itself, not client data, so a
# client edits their own content rather than the team that writes it.
# ---------------------------------------------------------------------------
@app.route("/api/agents", methods=["GET"])
@owner_required
def list_agents():
    return jsonify({
        "agents": agent_roster.list_agents(),
        "tools": tool_registry.describe(),
        "providers": llm.list_providers(),
        "router": {
            "provider": (os.environ.get("AGENT_ROUTER_PROVIDER") or "deepseek").strip(),
            "model": (os.environ.get("AGENT_ROUTER_MODEL") or "").strip(),
        },
        "max_messages": int(os.environ.get("AGENT_MAX_MESSAGES", "20")),
    })


@app.route("/api/agents", methods=["POST"])
@owner_required
def create_agent():
    # ValueError covers a missing name and a duplicate one; the roster cannot contain
    # two agents with the same name because AutoGen identifies participants by name.
    try:
        return jsonify({"agent": agent_roster.create_agent(request.get_json(silent=True) or {})}), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/agents/<int:agent_id>", methods=["PUT"])
@owner_required
def update_agent(agent_id):
    try:
        updated = agent_roster.update_agent(agent_id, request.get_json(silent=True) or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not updated:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"agent": updated})


@app.route("/api/agents/<int:agent_id>", methods=["DELETE"])
@owner_required
def delete_agent(agent_id):
    if not agent_roster.get_agent(agent_id):
        return jsonify({"error": "Not found"}), 404
    agent_roster.delete_agent(agent_id)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Servers (SSH targets the Operator may use)
#
# Platform-operator only. These are the *hosting fleet's* credentials, not client
# data: every route used to be @login_required, so any signed-in member could list
# the hosts, read their key paths, or delete them outright.
# ---------------------------------------------------------------------------
@app.route("/api/ssh-hosts", methods=["GET"])
@owner_required
def list_ssh_hosts():
    return jsonify({"hosts": servers_service.list_hosts(), "dry_run": actions.is_dry_run()})


@app.route("/api/ssh-hosts", methods=["POST"])
@owner_required
def create_ssh_host():
    data = request.get_json(silent=True) or {}
    if not (data.get("host") or "").strip():
        return jsonify({"error": "host is required"}), 400
    return jsonify({"host": servers_service.create_host(data)}), 201


@app.route("/api/ssh-hosts/<int:host_id>", methods=["PUT"])
@owner_required
def update_ssh_host(host_id):
    updated = servers_service.update_host(host_id, request.get_json(silent=True) or {})
    if not updated:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"host": updated})


@app.route("/api/ssh-hosts/<int:host_id>", methods=["DELETE"])
@owner_required
def delete_ssh_host(host_id):
    if not servers_service.get_host(host_id):
        return jsonify({"error": "Not found"}), 404
    servers_service.delete_host(host_id)
    return jsonify({"ok": True})


@app.route("/api/ssh-hosts/<int:host_id>/test", methods=["POST"])
@owner_required
def test_ssh_host(host_id):
    """Connect and run a read-only probe. Never mutates anything."""
    host = servers_service.get_host(host_id)
    if not host:
        return jsonify({"error": "Not found"}), 404
    target = "%s@%s:%s" % (host["username"], host["host"], host["port"])
    if actions.is_dry_run():
        return jsonify({
            "ok": False, "dry_run": True, "target": target,
            "output": "dry-run: set ALLOW_LIVE_ACTIONS=1 to probe a real server",
        })
    try:
        result = ssh_tool.check(host)
    except Exception as exc:
        result = {"ok": False, "target": target, "output": "%s: %s" % (type(exc).__name__, exc)}
    return jsonify(result)


# ---------------------------------------------------------------------------
# Sites (hosting)
# ---------------------------------------------------------------------------
def _site_for_request(site_id):
    """Load a site the caller is allowed to act on, or None.

    The platform owner may act on any client's site; a client only on one in their
    own org. Cross-tenant access returns 404 rather than 403 on purpose - a client
    should not be able to probe which site ids exist.
    """
    site = sites_service.get_site(site_id)
    if not site or not can_access_org(site.get("org_id")):
        return None
    return site


@app.route("/api/sites", methods=["GET"])
@login_required
def list_sites():
    """The caller's websites, plus the deploy targets they may choose.

    The server IP is included for everyone - a client has to point their DNS at it,
    which is the whole model. The SSH host list is *not*: it is the hosting fleet's
    own infrastructure, and it used to be returned to every signed-in user.
    """
    targets = [{"value": "local", "label": "This server (local)"}]
    if is_owner():
        for host in servers_service.list_hosts():
            if host["enabled"]:
                targets.append({
                    "value": str(host["id"]),
                    "label": "%s (%s@%s)" % (host["label"], host["username"], host["host"]),
                })
    return jsonify({
        "sites": sites_service.list_sites(list_scope()),
        "targets": targets,
        "dry_run": actions.is_dry_run(),
        "server_ip": provisioner.SERVER_IP,
    })


@app.route("/api/sites", methods=["POST"])
@client_admin_required
def create_site():
    data = request.get_json(silent=True) or {}
    domain = (data.get("domain") or "").strip().lower()
    if not tool_policy.check_domain(domain).allowed:
        return jsonify({"error": "That is not a valid domain name"}), 400
    # Domains stay globally unique: two clients cannot both own acme.com.
    if sites_service.get_site_by_domain(domain):
        return jsonify({"error": "That domain is already configured"}), 409
    # The tenant comes from the session, never from the payload.
    org_id = data.get("org_id") if is_owner() else current_org()
    return jsonify({
        "site": sites_service.create_site(
            data, owner_id=session["user_id"], org_id=org_id
        ),
    }), 201


@app.route("/api/sites/<int:site_id>", methods=["GET"])
@login_required
def get_site(site_id):
    site = _site_for_request(site_id)
    if not site:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"site": site})


@app.route("/api/sites/<int:site_id>", methods=["PUT"])
@client_admin_required
def update_site(site_id):
    if not _site_for_request(site_id):
        return jsonify({"error": "Not found"}), 404
    updated = sites_service.update_site(site_id, request.get_json(silent=True) or {})
    if not updated:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"site": updated})


@app.route("/api/sites/<int:site_id>", methods=["DELETE"])
@client_admin_required
def delete_site(site_id):
    if not _site_for_request(site_id):
        return jsonify({"error": "Not found"}), 404
    sites_service.delete_site(site_id)
    return jsonify({"ok": True})


@app.route("/api/sites/<int:site_id>/dns", methods=["GET"])
@login_required
def site_dns(site_id):
    """The exact DNS records to create, plus what the domain resolves to now."""
    site = _site_for_request(site_id)
    if not site:
        return jsonify({"error": "Not found"}), 404
    return jsonify({
        "site": site,
        "records": provisioner.dns_records(site["domain"]),
        "check": provisioner.check_dns(site["domain"]),
        "server_ip": provisioner.SERVER_IP,
    })


@app.route("/api/sites/<int:site_id>/check", methods=["POST"])
@login_required
def check_site(site_id):
    """Re-check DNS. Read-only, so it needs no approval."""
    site = _site_for_request(site_id)
    if not site:
        return jsonify({"error": "Not found"}), 404
    info = provisioner.check_dns(site["domain"])
    updated = sites_service.update_site(site_id, {
        "dns_last_ip": ", ".join(info["resolved"])[:200],
        "dns_last_check": now_str(),
        "status": "dns_ok" if (info["ok"] and site["status"] != "live") else site["status"],
    })
    return jsonify({"site": updated, "check": info})


@app.route("/api/sites/<int:site_id>/provision", methods=["POST"])
@client_admin_required
def provision_site(site_id):
    """Queue provisioning as a job, so its transcript and approvals stream live."""
    site = _site_for_request(site_id)
    if not site:
        return jsonify({"error": "Not found"}), 404
    job_id = jobs.create_job(
        "Provision %s" % site["domain"], site["domain"],
        session.get("user_id"), "selector", "site_provision",
        org_id=site.get("org_id"),
    )
    jobs.submit(job_id)
    return jsonify({"job": jobs.get_job(job_id), "site": site}), 201


# ---------------------------------------------------------------------------
# Approvals (the action gate in live mode)
# ---------------------------------------------------------------------------
@app.route("/api/approvals", methods=["GET"])
@login_required
def list_approvals():
    status = request.args.get("status") or None
    # Keyword: list_approvals(status, limit, org_id) would read the org as a limit.
    return jsonify({
        "approvals": actions.list_approvals(status, org_id=list_scope()),
        "dry_run": actions.is_dry_run(),
    })


@app.route("/api/approvals/<int:approval_id>/<decision>", methods=["POST"])
@login_required
def decide_approval(approval_id, decision):
    if decision not in ("approve", "deny"):
        return jsonify({"error": "decision must be approve or deny"}), 400
    if not actions.get_approval(approval_id):
        return jsonify({"error": "Not found"}), 404
    # A client may only decide the writes its own runs asked for. Deciding another
    # client's approval would mean authorising a write to their server.
    if not can_access_org(actions.approval_org_id(approval_id)):
        return jsonify({"error": "Not found"}), 404
    row = actions.decide_approval(
        approval_id,
        "approved" if decision == "approve" else "denied",
        decided_by=session.get("name") or session.get("email") or "operator",
    )
    return jsonify({"approval": row})


# ---------------------------------------------------------------------------
# Agent jobs (background AutoGen runs)
# ---------------------------------------------------------------------------
def _job_for_request(job_id):
    """Load a job the caller may see. 404 for another client's job, never 403."""
    job = jobs.get_job(job_id)
    if not job or not can_access_org(job.get("org_id")):
        return None
    return job


@app.route("/api/jobs", methods=["GET"])
@login_required
def list_jobs():
    # Keyword, not positional: list_jobs(limit, org_id) would read an org id as a
    # row limit and quietly return every client's runs.
    return jsonify({"jobs": jobs.list_jobs(org_id=list_scope())})


@app.route("/api/jobs", methods=["POST"])
@login_required
def create_job():
    data = request.get_json(silent=True) or {}
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "prompt is required"}), 400
    title = (data.get("title") or "").strip() or prompt[:60]
    task_name = (data.get("task") or jobs.DEFAULT_TASK).strip()
    if task_name not in jobs.TASKS:
        return jsonify({"error": "Unknown task: %s" % task_name}), 400
    team_mode = (data.get("team_mode") or "selector").strip()
    job_id = jobs.create_job(
        title, prompt, session.get("user_id"), team_mode, task_name,
        org_id=current_org(),
    )
    jobs.submit(job_id)
    return jsonify({"job": jobs.get_job(job_id)}), 201


@app.route("/api/jobs/<int:job_id>", methods=["GET"])
@login_required
def get_job(job_id):
    job = _job_for_request(job_id)
    if not job:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"job": job, "artifacts": jobs.list_artifacts(job_id)})


@app.route("/api/jobs/<int:job_id>/events", methods=["GET"])
@login_required
def job_events(job_id):
    job = _job_for_request(job_id)
    if not job:
        return jsonify({"error": "Not found"}), 404
    after = request.args.get("after", type=int) or 0
    return jsonify({
        "job": job,
        "events": jobs.get_events(job_id, after),
        "artifacts": jobs.list_artifacts(job_id),
    })


@app.route("/api/jobs/<int:job_id>/cancel", methods=["POST"])
@login_required
def cancel_job(job_id):
    if not _job_for_request(job_id):
        return jsonify({"error": "Not found"}), 404
    jobs.cancel_job(job_id)
    return jsonify({"job": jobs.get_job(job_id)})


# ---------------------------------------------------------------------------
# Generated artifacts (images the team produced)
#
# Public on purpose: a published page or an email renders <img src> with no session
# attached, so anything else would break the very thing the file exists for.
# send_from_directory refuses traversal, and filenames are per-job.
# ---------------------------------------------------------------------------
ARTIFACTS_ROOT = os.path.abspath(os.environ.get("ARTIFACTS_DIR") or "artifacts")


@app.route("/artifacts/<path:filename>", methods=["GET"])
def artifact_file(filename):
    return send_from_directory(ARTIFACTS_ROOT, filename, max_age=86400)


@app.route("/api/data-sources", methods=["GET"])
@client_admin_required
def list_data_sources():
    """Where this client's numbers come from. The operator may pass ?org_id=."""
    org = current_org()
    if org is None and is_owner() and request.args.get("org_id"):
        try:
            org = int(request.args["org_id"])
        except ValueError:
            return jsonify({"error": "org_id must be a number"}), 400
    return jsonify({
        "sources": analytics_service.sources(org),
        "kinds": [
            {"kind": kind, "label": analytics_service.LABELS[kind]}
            for kind in analytics_service.KINDS
        ],
    })


@app.route("/api/data-sources", methods=["POST"])
@client_admin_required
def save_data_source():
    data = request.get_json(silent=True) or {}
    org = current_org()
    if org is None:
        org = data.get("org_id")
        org = int(org) if org not in (None, "") else None
    try:
        source = analytics_service.save_source(
            data, source_id=data.get("id") or None, org_id=org
        )
    except analytics_service.SourceError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"source": source}), 200 if data.get("id") else 201


@app.route("/api/data-sources/sync", methods=["POST"])
@client_admin_required
def sync_data_sources():
    """Pull the site logs now, instead of waiting for the daily run.

    Operator-only even though the data it writes is attributed per client: reading the
    whole log is a deployment-level action, and a client triggering it repeatedly would
    just burn the same work for everyone.
    """
    if not is_owner():
        return jsonify({"error": "Forbidden"}), 403
    import site_logs

    return jsonify(site_logs.sync())


@app.route("/api/data-sources/<int:source_id>", methods=["DELETE"])
@client_admin_required
def delete_data_source(source_id):
    existing = analytics_service.get_source(source_id)
    if not existing or not can_access_org(existing.get("org_id")):
        return jsonify({"error": "Not found"}), 404
    analytics_service.delete_source(source_id)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Providers (agent model routing)
# ---------------------------------------------------------------------------
@app.route("/api/providers", methods=["GET"])
@owner_required
def providers():
    """Which providers the platform's keys configure.

    Operator information: the agents are site-wide and run on the platform's keys, so
    a client has no business enumerating them. This route had no guard at all.
    """
    return jsonify({
        "providers": llm.list_providers(),
        "configured": llm.configured_providers(),
        "default": llm.DEFAULT_PROVIDER,
    })


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.route("/api/health", methods=["GET"])
def health():
    with get_connection() as conn:
        bot_count = conn.execute("SELECT COUNT(*) FROM bots").fetchone()[0]
        user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        channel_count = conn.execute("SELECT COUNT(*) FROM channels").fetchone()[0]
        agent_count = conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
        job_count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    return jsonify({
        "status": "ok",
        "model": deepseek_config()[2],
        "api_key_configured": bool(deepseek_config()[0]),
        "providers_configured": llm.configured_providers(),
        "brand": brand(),
        "bots": bot_count,
        "users": user_count,
        "channels": channel_count,
        "agents": agent_count,
        "jobs": job_count,
        "job_backend": jobs.JOB_BACKEND,
        "database": "postgresql" if os.environ.get("DATABASE_URL", "").startswith("postgres") else "sqlite",
    })


# ---------------------------------------------------------------------------
# Admin panel + widget static files
# ---------------------------------------------------------------------------
ADMIN_DIR = os.path.join(BASE_DIR, "admin")
WIDGET_DIR = os.path.join(BASE_DIR, "widget")


@app.route("/")
def root():
    return redirect("/admin/")


@app.route("/admin")
def admin_index():
    return redirect("/admin/")


@app.route("/admin/")
def admin_slash():
    return send_from_directory(ADMIN_DIR, "index.html")


@app.route("/admin/<path:filename>")
def admin_assets(filename):
    return send_from_directory(ADMIN_DIR, filename)


@app.route("/widget.js")
def widget_js():
    """The embeddable widget.

    It is loaded from pages we do not control, so a redesign has to reach existing
    embeds in minutes rather than whenever a browser decides to revalidate. Werkzeug
    still sends ETag/Last-Modified, so the steady-state cost is a 304.
    """
    response = send_from_directory(WIDGET_DIR, "widget.js")
    response.headers["Cache-Control"] = "public, max-age=300, must-revalidate"
    return response


@app.route("/demo")
def demo():
    return send_from_directory(WIDGET_DIR, "index.html")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
init_db()
seed_initial_data()

# Seed the default agent roster (no-op once agents exist).
agent_roster.ensure_roster()

# Start the in-process job runner (idempotent). With JOB_BACKEND=redis the
# app enqueues onto the arq worker instead; the runner stays idle.
jobs.get_runner().start()

if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    app.run(host=host, port=port, debug=False)
