"""Private session history for the logged-in Assistant.

Each session belongs to one user and the client they were acting as. The transcript
lives here so it survives a browser change; the manager job is still the place to see
what tools ran. A message's `job_id` is the bridge between the two.
"""

from db import get_connection, now_str

ROLES = ("user", "assistant")
MAX_TITLE = 90
MAX_CONTENT = 20000
MAX_IMPORT = 100


class AssistantSessionError(Exception):
    """Invalid session/message input; safe to show the user."""


def _as_dict(row):
    return {key: row[key] for key in row.keys()}


def _title(value):
    text = " ".join(str(value or "").split()).strip()
    return text[:MAX_TITLE] if text else ""


def _content(value):
    text = str(value or "").replace("\x00", "").strip()
    if not text:
        raise AssistantSessionError("A message cannot be empty.")
    if len(text) > MAX_CONTENT:
        raise AssistantSessionError(
            "That message is longer than the %d character limit." % MAX_CONTENT)
    return text


def _role(value):
    role = str(value or "").strip().lower()
    if role not in ROLES:
        raise AssistantSessionError("role must be user or assistant.")
    return role


def _org_clause(alias, org_id):
    """The scoped org predicate; None means the operator's no-client view."""
    if org_id is None:
        return alias + "org_id IS NULL", []
    return alias + "org_id = ?", [org_id]


def create_session(user_id, org_id=None, title=""):
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO assistant_sessions (org_id, user_id, title) VALUES (?, ?, ?)",
            (org_id, user_id, _title(title)),
        )
        session_id = cur.lastrowid
    return get_session(session_id, user_id, org_id)


def get_session(session_id, user_id, org_id=None):
    """One session only if it belongs to this user and acting client."""
    clause, params = _org_clause("", org_id)
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM assistant_sessions WHERE id = ? AND user_id = ? AND " + clause,
            tuple([session_id, user_id] + params),
        ).fetchone()
    return _as_dict(row) if row else None


def list_sessions(user_id, org_id=None, limit=50):
    """Sessions with at least one message, newest activity first."""
    clause, params = _org_clause("s.", org_id)
    sql = (
        "SELECT s.*, (SELECT COUNT(*) FROM assistant_messages m "
        "WHERE m.session_id = s.id) AS message_count "
        "FROM assistant_sessions s WHERE s.user_id = ? AND " + clause +
        " AND EXISTS (SELECT 1 FROM assistant_messages m2 WHERE m2.session_id = s.id) "
        "ORDER BY s.updated_at DESC, s.id DESC LIMIT ?"
    )
    with get_connection() as conn:
        rows = conn.execute(sql, tuple([user_id] + params + [max(1, int(limit))])).fetchall()
    return [_as_dict(row) for row in rows]


def list_messages(session_id, limit=200):
    """The last N messages, returned oldest first."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, session_id, role, content, job_id, created_at "
            "FROM assistant_messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, max(1, int(limit))),
        ).fetchall()
    return [_as_dict(row) for row in reversed(rows)]


def get_session_with_messages(session_id, user_id, org_id=None, limit=200):
    session = get_session(session_id, user_id, org_id)
    if not session:
        return None
    return {"session": session, "messages": list_messages(session_id, limit)}


def add_message(session_id, role, content, job_id=None):
    """Append a turn, update the session's activity and set the first title."""
    role = _role(role)
    content = _content(content)
    with get_connection() as conn:
        session = conn.execute(
            "SELECT id, title FROM assistant_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not session:
            raise AssistantSessionError("That chat no longer exists.")
        cur = conn.execute(
            "INSERT INTO assistant_messages (session_id, role, content, job_id) "
            "VALUES (?, ?, ?, ?)",
            (session_id, role, content, job_id),
        )
        message_id = cur.lastrowid
        title = session["title"]
        if role == "user" and not (title or "").strip():
            title = _title(content) or "New chat"
        conn.execute(
            "UPDATE assistant_sessions SET updated_at = ?, title = ? WHERE id = ?",
            (now_str(), title or "", session_id),
        )
        row = conn.execute(
            "SELECT id, session_id, role, content, job_id, created_at "
            "FROM assistant_messages WHERE id = ?", (message_id,)
        ).fetchone()
    return _as_dict(row)


def record_reply_for_job(job_id, content):
    """Store the manager's answer against the user turn that started the job."""
    if not job_id:
        return None
    content = _content(content)
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id, session_id, role, content, job_id, created_at "
            "FROM assistant_messages WHERE job_id = ? AND role = 'assistant' "
            "ORDER BY id ASC LIMIT 1", (job_id,)
        ).fetchone()
        if existing:
            return _as_dict(existing)
        user_turn = conn.execute(
            "SELECT session_id FROM assistant_messages WHERE job_id = ? "
            "AND role = 'user' ORDER BY id ASC LIMIT 1", (job_id,)
        ).fetchone()
        if not user_turn:
            return None
        cur = conn.execute(
            "INSERT INTO assistant_messages (session_id, role, content, job_id) "
            "VALUES (?, 'assistant', ?, ?)",
            (user_turn["session_id"], content, job_id),
        )
        row = conn.execute(
            "SELECT id, session_id, role, content, job_id, created_at "
            "FROM assistant_messages WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        conn.execute(
            "UPDATE assistant_sessions SET updated_at = ? WHERE id = ?",
            (now_str(), user_turn["session_id"]),
        )
    return _as_dict(row)


def import_messages(user_id, org_id, messages, title=""):
    """One-time import of the old browser thread into a real session."""
    if not isinstance(messages, list) or not messages:
        raise AssistantSessionError("There is no previous thread to import.")
    cleaned = []
    for item in messages[:MAX_IMPORT]:
        if not isinstance(item, dict):
            continue
        try:
            role = _role(item.get("role"))
            content = _content(item.get("content"))
        except AssistantSessionError:
            continue
        cleaned.append((role, content))
    if not cleaned:
        raise AssistantSessionError("There is no previous thread to import.")
    first_user = next((content for role, content in cleaned if role == "user"), "")
    chosen = _title(title) or _title(first_user) or "Previous chat"
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO assistant_sessions (org_id, user_id, title) VALUES (?, ?, ?)",
            (org_id, user_id, chosen),
        )
        session_id = cur.lastrowid
        for role, content in cleaned:
            conn.execute(
                "INSERT INTO assistant_messages (session_id, role, content) "
                "VALUES (?, ?, ?)", (session_id, role, content),
            )
        conn.execute(
            "UPDATE assistant_sessions SET updated_at = ? WHERE id = ?",
            (now_str(), session_id),
        )
    session = get_session(session_id, user_id, org_id)
    return {"session": session, "imported": len(cleaned)}


