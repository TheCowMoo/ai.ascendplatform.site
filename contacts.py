"""Contacts: the mini CRM behind a captured lead.

A lead is written here first and synchronised to GoHighLevel afterwards, never the other
way round - a form submission must not fail because a CRM provider is down.
"""

import json

from db import get_connection, now_str

STATUSES = ("new", "contacted", "qualified", "customer", "archived")
SOURCES = ("form", "widget", "webhook", "manual", "ghl")
TEXT_FIELDS = ("name", "email", "phone", "source", "status", "notes",
               "ghl_contact_id", "ghl_synced_at", "ghl_error")


class ContactError(ValueError):
    pass


def contact_out(row):
    if not row:
        return None
    out = dict(row)
    raw = out.get("tags") or "[]"
    try:
        out["tags"] = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except (TypeError, ValueError):
        out["tags"] = []
    return out


def create_contact(data, org_id, source=None):
    """Insert a contact. ``email`` is the only thing that has to be there."""
    data = data or {}
    email = (data.get("email") or "").strip().lower()
    if not email and not (data.get("name") or "").strip():
        raise ContactError("A contact needs at least a name or an email.")
    status = (data.get("status") or "new").strip().lower()
    if status not in STATUSES:
        raise ContactError("Unknown status: %s" % status)
    src = (source or data.get("source") or "manual").strip().lower()
    if src not in SOURCES:
        src = "manual"
    tags = data.get("tags")
    if isinstance(tags, str):
        tags = [part.strip() for part in tags.split(",") if part.strip()]
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO contacts (org_id, name, email, phone, source, status, tags, notes, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (org_id, (data.get("name") or "").strip()[:200], email[:200],
             (data.get("phone") or "").strip()[:60], src, status, json.dumps(tags or []),
             (data.get("notes") or "")[:4000], now_str(), now_str()),
        )
        row = conn.execute("SELECT * FROM contacts WHERE id = ?", (cur.lastrowid,)).fetchone()
    item = contact_out(row)
    # The event fires *after* the row exists, and cannot fail the capture: a broken
    # automation must never lose a lead.
    try:
        import automations

        automations.start_for_event(org_id, "contact.created",
                                    context={"contact.id": item["id"]})
    except Exception:
        pass
    return item


def list_contacts(org_id, status=None, limit=200):
    query = "SELECT * FROM contacts WHERE org_id = ?"
    params = [org_id]
    if status:
        query += " AND status = ?"
        params.append(status)
    with get_connection() as conn:
        rows = conn.execute(query + " ORDER BY id DESC LIMIT %d" % max(1, int(limit)),
                            tuple(params)).fetchall()
    return [contact_out(row) for row in rows]


def get_contact(contact_id, org_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM contacts WHERE id = ? AND org_id = ?",
                           (contact_id, org_id)).fetchone()
    return contact_out(row)


def update_contact(contact_id, org_id, data):
    if not get_contact(contact_id, org_id):
        return None
    sets, values = [], []
    for field in TEXT_FIELDS:
        if field in (data or {}):
            value = data.get(field)
            sets.append("%s = ?" % field)
            values.append(value.strip() if isinstance(value, str) else (value or ""))
    if "tags" in (data or {}):
        raw = data.get("tags")
        if isinstance(raw, str):
            raw = [part.strip() for part in raw.split(",") if part.strip()]
        sets.append("tags = ?")
        values.append(json.dumps(raw or []))
    if not sets:
        return get_contact(contact_id, org_id)
    sets.append("updated_at = ?")
    values.append(now_str())
    values.extend([contact_id, org_id])
    with get_connection() as conn:
        conn.execute("UPDATE contacts SET %s WHERE id = ? AND org_id = ?" % ", ".join(sets),
                     tuple(values))
    return get_contact(contact_id, org_id)
