"""Internal agency bug tracking.

A bug belongs to the agency, not to a tenant. ``org_id`` is optional context for the
client affected by the issue; it never grants a client access to the row. Comments and
field changes share one ``bug_events`` timeline so the history cannot be edited away.
"""

import json

from db import get_connection, now_str

STATUSES = ("open", "in_progress", "resolved", "closed")
PRIORITIES = ("low", "normal", "high", "urgent")

STATUS_LABELS = {
    "open": "Open",
    "in_progress": "In progress",
    "resolved": "Resolved",
    "closed": "Closed",
}
PRIORITY_LABELS = {
    "low": "Low",
    "normal": "Normal",
    "high": "High",
    "urgent": "Urgent",
}


class BugError(ValueError):
    """A validation problem the agency user can fix."""


def _as_dict(row):
    return {key: row[key] for key in row.keys()}


def out(row):
    item = _as_dict(row)
    item["status_label"] = STATUS_LABELS.get(item.get("status"), item.get("status") or "")
    item["priority_label"] = PRIORITY_LABELS.get(item.get("priority"),
                                                  item.get("priority") or "")
    item["org_name"] = item.get("org_name") or ""
    item["reporter_name"] = item.get("reporter_name") or ""
    item["assignee_name"] = item.get("assignee_name") or ""
    return item


_BUG_SELECT = (
    "SELECT b.*, o.name AS org_name, "
    "r.name AS reporter_name, r.email AS reporter_email, "
    "a.name AS assignee_name, a.email AS assignee_email "
    "FROM bugs b "
    "LEFT JOIN orgs o ON o.id = b.org_id "
    "LEFT JOIN users r ON r.id = b.reporter_id "
    "LEFT JOIN users a ON a.id = b.assignee_id"
)


def get_bug(bug_id):
    with get_connection() as conn:
        row = conn.execute(_BUG_SELECT + " WHERE b.id = ?", (bug_id,)).fetchone()
    return out(row) if row else None


def events(bug_id, limit=200):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT e.*, u.name AS actor_name, u.email AS actor_email "
            "FROM bug_events e LEFT JOIN users u ON u.id = e.actor_id "
            "WHERE e.bug_id = ? ORDER BY e.id ASC LIMIT ?",
            (bug_id, max(1, min(int(limit), 500))),
        ).fetchall()
    result = []
    for row in rows:
        item = _as_dict(row)
        try:
            item["detail"] = json.loads(item.get("detail") or "{}")
        except (TypeError, ValueError):
            item["detail"] = {}
        result.append(item)
    return result


def assignees():
    """Active agency owners who can be assigned a bug."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, email FROM users "
            "WHERE role IN ('owner', 'admin') AND COALESCE(status, 'active') = 'active' "
            "ORDER BY LOWER(name), LOWER(email)"
        ).fetchall()
    return [_as_dict(row) for row in rows]


def counts():
    result = {status: 0 for status in STATUSES}
    with get_connection() as conn:
        rows = conn.execute("SELECT status, COUNT(*) AS n FROM bugs GROUP BY status").fetchall()
    for row in rows:
        if row["status"] in result:
            result[row["status"]] = row["n"]
    result["all"] = sum(result.values())
    return result


def list_bugs(status="", priority="", assignee_id=None, org_id=None, q="", limit=200):
    query = _BUG_SELECT + " WHERE 1 = 1"
    params = []
    status = (status or "").strip()
    if status:
        if status not in STATUSES:
            raise BugError("Unknown bug status: %s" % status)
        query += " AND b.status = ?"
        params.append(status)
    priority = (priority or "").strip()
    if priority:
        if priority not in PRIORITIES:
            raise BugError("Unknown bug priority: %s" % priority)
        query += " AND b.priority = ?"
        params.append(priority)
    if assignee_id not in (None, ""):
        if str(assignee_id).lower() in ("unassigned", "0"):
            query += " AND b.assignee_id IS NULL"
        else:
            try:
                params.append(int(assignee_id))
            except (TypeError, ValueError):
                raise BugError("Assignee must be a number.")
            query += " AND b.assignee_id = ?"
    if org_id not in (None, ""):
        try:
            params.append(int(org_id))
        except (TypeError, ValueError):
            raise BugError("Client must be a number.")
        query += " AND b.org_id = ?"
    if q:
        needle = "%%%s%%" % str(q).strip().lower()
        query += " AND (LOWER(b.title) LIKE ? OR LOWER(b.description) LIKE ?)"
        params.extend([needle, needle])
    query += " ORDER BY b.updated_at DESC, b.id DESC LIMIT ?"
    params.append(max(1, min(int(limit or 200), 500)))
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [out(row) for row in rows]


def _clean_title(value):
    title = str(value or "").strip()
    if not title:
        raise BugError("Give the bug a short title.")
    return title[:200]


def _clean_description(value):
    return str(value or "").strip()[:12000]


def _clean_status(value):
    status = str(value or "open").strip().lower().replace(" ", "_")
    if status not in STATUSES:
        raise BugError("Status must be one of: %s" % ", ".join(STATUSES))
    return status


def _clean_priority(value):
    priority = str(value or "normal").strip().lower()
    if priority not in PRIORITIES:
        raise BugError("Priority must be one of: %s" % ", ".join(PRIORITIES))
    return priority


def _clean_page(value):
    return str(value or "").strip()[:300]


def _clean_org(value):
    if value in (None, ""):
        return None
    try:
        org_id = int(value)
    except (TypeError, ValueError):
        raise BugError("Client must be a number.")
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM orgs WHERE id = ?", (org_id,)).fetchone()
    if not row:
        raise BugError("That client does not exist.")
    return org_id


def _clean_assignee(value):
    if value in (None, "", "0"):
        return None
    try:
        user_id = int(value)
    except (TypeError, ValueError):
        raise BugError("Assignee must be a number.")
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE id = ? AND role IN ('owner', 'admin') "
            "AND COALESCE(status, 'active') = 'active'",
            (user_id,),
        ).fetchone()
    if not row:
        raise BugError("Choose an active agency user as the assignee.")
    return user_id


def _event(bug_id, actor_id, action, detail=None):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO bug_events (bug_id, actor_id, action, detail, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (bug_id, actor_id, action, json.dumps(detail or {}, separators=(",", ":")),
             now_str()),
        )


def create_bug(data, reporter_id):
    data = data or {}
    title = _clean_title(data.get("title"))
    description = _clean_description(data.get("description"))
    status = _clean_status(data.get("status") or "open")
    priority = _clean_priority(data.get("priority") or "normal")
    org_id = _clean_org(data.get("org_id"))
    assignee_id = _clean_assignee(data.get("assignee_id"))
    page = _clean_page(data.get("page"))
    resolved_at = now_str() if status == "resolved" else None
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO bugs (title, description, status, priority, org_id, reporter_id, "
            "assignee_id, page, resolved_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (title, description, status, priority, org_id, reporter_id, assignee_id,
             page, resolved_at, now_str()),
        )
        bug_id = cur.lastrowid
    _event(bug_id, reporter_id, "created", {
        "status": status, "priority": priority, "assignee_id": assignee_id,
    })
    return get_bug(bug_id)


def update_bug(bug_id, data, actor_id):
    data = data or {}
    before = get_bug(bug_id)
    if not before:
        raise BugError("That bug does not exist.")

    title = _clean_title(data["title"]) if "title" in data else before["title"]
    description = (_clean_description(data["description"])
                   if "description" in data else before["description"])
    status = _clean_status(data["status"]) if "status" in data else before["status"]
    priority = (_clean_priority(data["priority"])
                if "priority" in data else before["priority"])
    org_id = _clean_org(data["org_id"]) if "org_id" in data else before["org_id"]
    assignee_id = (_clean_assignee(data["assignee_id"])
                   if "assignee_id" in data else before["assignee_id"])
    page = _clean_page(data["page"]) if "page" in data else before.get("page", "")

    resolved_at = before.get("resolved_at")
    if status == "resolved" and before["status"] != "resolved":
        resolved_at = now_str()
    elif status != "resolved":
        resolved_at = None

    with get_connection() as conn:
        conn.execute(
            "UPDATE bugs SET title = ?, description = ?, status = ?, priority = ?, "
            "org_id = ?, assignee_id = ?, page = ?, resolved_at = ?, updated_at = ? "
            "WHERE id = ?",
            (title, description, status, priority, org_id, assignee_id, page,
             resolved_at, now_str(), bug_id),
        )

    changes = (
        ("status", "status", before["status"], status),
        ("priority", "priority", before["priority"], priority),
        ("assigned", "assignee_id", before.get("assignee_id"), assignee_id),
        ("updated", "org_id", before.get("org_id"), org_id),
    )
    for action, field, old, new in changes:
        if old != new:
            _event(bug_id, actor_id, action, {"field": field, "from": old, "to": new})
    if (title != before["title"] or description != before["description"]
            or page != before.get("page", "")):
        _event(bug_id, actor_id, "updated", {
            "title": title != before["title"],
            "description": description != before["description"],
            "page": page != before.get("page", ""),
        })
    return get_bug(bug_id)


def add_comment(bug_id, text, actor_id):
    if not get_bug(bug_id):
        raise BugError("That bug does not exist.")
    comment = str(text or "").strip()
    if not comment:
        raise BugError("Write a comment first.")
    if len(comment) > 12000:
        raise BugError("That comment is too long.")
    _event(bug_id, actor_id, "commented", {"comment": comment})
    with get_connection() as conn:
        conn.execute("UPDATE bugs SET updated_at = ? WHERE id = ?", (now_str(), bug_id))
    return get_bug(bug_id)
