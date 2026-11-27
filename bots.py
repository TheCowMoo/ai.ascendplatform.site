"""Chat assistants (bots): tenant-scoped access for the Manager and the team.

The Flask routes in ``app.py`` predate this module and still own their request
session checks. The agent tools cannot use a Flask session, so they use these
functions instead: every lookup takes the org from the job row, never from a tool
argument, and the serializer never includes channel tokens or the webhook URL.
"""

import json
import re

import profile as profile_mod
from db import get_connection, new_bot_id, now_str

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


class BotError(ValueError):
    """A field the caller can fix, phrased for the person who asked."""


def _as_bool(value, default=True):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("0", "false", "no", "off", "")


def _text(value, limit):
    return str(value or "").strip()[:limit]


def _quick_replies(value):
    if value in (None, ""):
        return []
    if isinstance(value, str):
        raw = value.strip()
        try:
            parsed = json.loads(raw)
            value = parsed if isinstance(parsed, list) else raw.split(",")
        except ValueError:
            value = raw.split(",")
    if not isinstance(value, (list, tuple)):
        raise BotError("Quick replies must be a list of short sentences.")
    out = []
    for item in value:
        text = str(item or "").strip()[:80]
        if text and text not in out:
            out.append(text)
    return out[:8]


def _brand(value):
    text = str(value or "").strip()
    if not text:
        return ""
    if not _HEX.match(text):
        raise BotError("Brand colour must be a hex colour such as #4f46e5.")
    return text


def _logo(value):
    text = str(value or "").strip()
    if not text:
        return ""
    if not (text.startswith("https://") or text.startswith("/media/")):
        raise BotError("The logo must be an https URL or a /media/ path.")
    return text[:1000]


def bot_out(row, message_count=None, include_webhook=False):
    """Serialise a bot row. Tokens and webhook URLs stay out by default."""
    if not row:
        return None
    out = dict(row)
    raw = out.get("quick_replies") or "[]"
    try:
        out["quick_replies"] = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except (TypeError, ValueError):
        out["quick_replies"] = []
    out["use_profile_kb"] = bool(out.get("use_profile_kb", 1))
    if not include_webhook:
        out.pop("webhook_url", None)
    if message_count is not None:
        out["message_count"] = int(message_count or 0)
    return out


def list_bots(org_id, limit=100):
    """This client's assistants, newest first, with message counts."""
    if org_id is None:
        return []
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT b.*, COUNT(m.id) AS message_count FROM bots b "
            "LEFT JOIN messages m ON m.bot_id = b.id "
            "WHERE b.org_id = ? GROUP BY b.id ORDER BY b.created_at DESC LIMIT ?",
            (org_id, max(1, int(limit))),
        ).fetchall()
    return [bot_out(row, row["message_count"]) for row in rows]


def get_bot(bot_id, org_id):
    """One bot scoped to the client. Another client's id is simply not found."""
    bot_id = str(bot_id or "").strip()
    if not bot_id or org_id is None:
        return None
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM bots WHERE id = ? AND org_id = ?", (bot_id, org_id)
        ).fetchone()
    return bot_out(row)


def create_bot(data, org_id, owner_id=None):
    """Create a bot for a client. No webhook or channel credential is accepted."""
    data = data or {}
    if org_id is None:
        raise BotError("Pick a client first - an assistant belongs to a client.")
    name = _text(data.get("name"), 120)
    if not name:
        raise BotError("A chat assistant needs a name.")
    with get_connection() as conn:
        duplicate = conn.execute(
            "SELECT id FROM bots WHERE org_id = ? AND lower(name) = lower(?)",
            (org_id, name),
        ).fetchone()
    if duplicate:
        raise BotError("A chat assistant called %r already exists (#%s); update that one."
                       % (name, duplicate["id"]))
    profile = profile_mod.ensure_profile(org_id, owner_id) or {}
    bot_id = new_bot_id()
    quick = _quick_replies(data.get("quick_replies"))
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO bots (id, name, assistant_name, brand_color, logo_url, "
            "welcome_message, quick_replies, system_prompt, knowledge_base, webhook_url, "
            "profile_id, use_profile_kb, owner_id, org_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?)",
            (
                bot_id,
                name,
                _text(data.get("assistant_name"), 80) or "Assistant",
                _brand(data.get("brand_color")),
                _logo(data.get("logo_url")),
                _text(data.get("welcome_message"), 500),
                json.dumps(quick),
                _text(data.get("system_prompt"), 4000),
                str(data.get("knowledge_base") or "")[:30000],
                profile.get("id"),
                1 if _as_bool(data.get("use_profile_kb"), True) else 0,
                owner_id,
                org_id,
            ),
        )
    return get_bot(bot_id, org_id)


def update_bot(bot_id, org_id, data):
    """Update the copy/configuration the agent is allowed to edit."""
    current = get_bot(bot_id, org_id)
    if not current:
        return None
    data = data or {}
    name = _text(data.get("name"), 120) or current["name"]
    assistant_name = (_text(data.get("assistant_name"), 80)
                      or current.get("assistant_name") or "Assistant")
    brand = (_brand(data.get("brand_color")) if "brand_color" in data
             else current.get("brand_color") or "")
    logo = (_logo(data.get("logo_url")) if "logo_url" in data
            else current.get("logo_url") or "")
    welcome = (_text(data.get("welcome_message"), 500)
               if "welcome_message" in data else current.get("welcome_message") or "")
    quick = (_quick_replies(data.get("quick_replies"))
             if "quick_replies" in data else current.get("quick_replies") or [])
    system_prompt = (_text(data.get("system_prompt"), 4000)
                     if "system_prompt" in data else current.get("system_prompt") or "")
    knowledge = (str(data.get("knowledge_base") or "")[:30000]
                 if "knowledge_base" in data else current.get("knowledge_base") or "")
    use_profile_kb = (_as_bool(data.get("use_profile_kb"),
                               current.get("use_profile_kb", True))
                      if "use_profile_kb" in data
                      else current.get("use_profile_kb", True))
    with get_connection() as conn:
        conn.execute(
            "UPDATE bots SET name=?, assistant_name=?, brand_color=?, logo_url=?, "
            "welcome_message=?, quick_replies=?, system_prompt=?, knowledge_base=?, "
            "use_profile_kb=?, updated_at=? WHERE id=? AND org_id=?",
            (name, assistant_name, brand, logo, welcome, json.dumps(quick),
             system_prompt, knowledge, 1 if use_profile_kb else 0,
             now_str(), bot_id, org_id),
        )
    return get_bot(bot_id, org_id)
