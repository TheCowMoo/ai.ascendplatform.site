"""
The content a client publishes (the Websites, Blogs, Funnels, Emails and Library
tabs).

A **deliverable** is the thing that goes live: a blog post, a funnel step, an email
campaign. It is not an artifact (a file a run emitted) and not a site (the place it
lands), which is why it has its own table and its own lifecycle:

    draft -> scheduled -> published        (or failed, from any of them)

``kind`` carries the tab it belongs to. The Library tab shows every kind, because
"everything the team has produced for you" is exactly the union of them.
"""

import re

from db import get_connection, now_str

KINDS = ("blog", "funnel", "email", "asset")
STATUSES = ("draft", "scheduled", "published", "failed")

TEXT_FIELDS = ("title", "slug", "status", "summary", "content", "url")


class DeliverableError(Exception):
    """Raised for invalid input."""


def _as_dict(row):
    return {k: row[k] for k in row.keys()}


def slugify(value):
    text = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower())
    return text.strip("-")[:80]


def out(row):
    item = _as_dict(row)
    for key in ("summary", "content", "url", "slug"):
        item[key] = item.get(key) or ""
    return item


def list_deliverables(kind=None, org_id=None, limit=200):
    """Deliverables for one client, newest first.

    ``org_id=None`` is the platform operator's view and returns every client's.
    ``kind=None`` returns all of them - which is the Library tab.
    """
    query = "SELECT * FROM deliverables WHERE 1 = 1"
    params = []
    if org_id is not None:
        query += " AND org_id = ?"
        params.append(org_id)
    if kind:
        query += " AND kind = ?"
        params.append(kind)
    query += " ORDER BY updated_at DESC, id DESC LIMIT ?"
    params.append(max(1, int(limit)))
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [out(row) for row in rows]


def get_deliverable(deliverable_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM deliverables WHERE id = ?", (deliverable_id,)
        ).fetchone()
    return out(row) if row else None


def counts(org_id=None):
    """``{kind: count}`` plus ``all``, for the tab badges."""
    query = "SELECT kind, COUNT(*) AS n FROM deliverables WHERE 1 = 1"
    params = []
    if org_id is not None:
        query += " AND org_id = ?"
        params.append(org_id)
    query += " GROUP BY kind"
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    result = {kind: 0 for kind in KINDS}
    total = 0
    for row in rows:
        result[row["kind"]] = row["n"]
        total += row["n"]
    result["all"] = total
    return result


def _clean(data, partial=False):
    """Validate a payload. ``partial`` keeps absent fields absent, for a PATCH."""
    kind = (data.get("kind") or "blog").strip().lower()
    if kind not in KINDS:
        raise DeliverableError("kind must be one of: %s" % ", ".join(KINDS))
    status = (data.get("status") or "draft").strip().lower()
    if status not in STATUSES:
        raise DeliverableError("status must be one of: %s" % ", ".join(STATUSES))
    title = (data.get("title") or "").strip()
    if not partial and not title:
        raise DeliverableError("A title is required")
    return kind, status, title


def create_deliverable(data, org_id=None, created_by=None):
    data = data or {}
    kind, status, title = _clean(data)
    site_id = data.get("site_id")
    try:
        site_id = int(site_id) if site_id not in (None, "") else None
    except (TypeError, ValueError):
        site_id = None
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO deliverables (org_id, site_id, kind, title, slug, status, "
            "summary, content, url, job_id, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                org_id,
                site_id,
                kind,
                title,
                (data.get("slug") or slugify(title)),
                status,
                data.get("summary") or "",
                data.get("content") or "",
                (data.get("url") or "").strip(),
                data.get("job_id"),
                created_by,
            ),
        )
        deliverable_id = cur.lastrowid
    return get_deliverable(deliverable_id)


def update_deliverable(deliverable_id, data):
    """Patch a deliverable. Only the keys present are touched."""
    existing = get_deliverable(deliverable_id)
    if not existing:
        return None
    data = data or {}
    merged = dict(existing)
    merged.update({k: v for k, v in data.items() if k in TEXT_FIELDS or k in ("kind", "site_id")})
    kind, status, title = _clean(merged, partial=True)

    sets = ["kind = ?", "status = ?", "title = ?", "updated_at = ?"]
    values = [kind, status, title, now_str()]
    for field in ("slug", "summary", "content", "url"):
        if field in data:
            sets.append("%s = ?" % field)
            value = data.get(field)
            values.append(value.strip() if isinstance(value, str) else (value or ""))
    if "site_id" in data:
        sets.append("site_id = ?")
        try:
            values.append(int(data["site_id"]) if data["site_id"] not in (None, "") else None)
        except (TypeError, ValueError):
            values.append(None)
    values.append(deliverable_id)
    with get_connection() as conn:
        conn.execute(
            "UPDATE deliverables SET %s WHERE id = ?" % ", ".join(sets), values
        )
    return get_deliverable(deliverable_id)


def delete_deliverable(deliverable_id):
    with get_connection() as conn:
        conn.execute("DELETE FROM deliverables WHERE id = ?", (deliverable_id,))
    return True
