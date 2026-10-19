"""The platform's own mail - invitations and operator notices - through GoHighLevel.

Not to be confused with ``tools/mail.py``, which sends a *client's* mail through that
client's SMTP. This module is the operator's channel: the token it uses is the
platform's, and the settings group that holds it is ``platform``-scoped so a client can
never resolve it.

GHL's API has one awkward property worth knowing before reading on: it cannot send to
an email address. It sends to a **contact**, so every message is an upsert followed by
a conversation message. Both halves are then visible in the CRM, which is the reason
for using GHL here rather than plain SMTP.

Everything is read from settings per call, so the panel applies without a restart, and
the HTTP call goes through ``_transport`` so tests can drive the whole flow offline.
"""

import json
import urllib.error
import urllib.request

import settings
from db import get_connection, now_str

DEFAULT_BASE = "https://services.leadconnectorhq.com"
DEFAULT_VERSION = "2021-07-28"

TEXT_FIELDS = ("name", "from_name", "from_email", "reply_to", "signature", "logo_url")


class MailError(Exception):
    """A send or a connection check failed, with a message fit for the operator."""


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------
def connection():
    """The GHL credentials and endpoint, resolved from settings."""
    return {
        "token": settings.get("GHL_API_TOKEN"),
        "location": settings.get("GHL_LOCATION_ID"),
        "version": settings.get("GHL_API_VERSION", DEFAULT_VERSION) or DEFAULT_VERSION,
        "base": (settings.get("GHL_API_BASE", DEFAULT_BASE) or DEFAULT_BASE).rstrip("/"),
    }


def is_configured():
    conn = connection()
    return bool(conn["token"] and conn["location"])


def _headers(conn):
    return {
        "Authorization": "Bearer %s" % conn["token"],
        "Version": conn["version"],
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _transport(method, url, headers, payload=None, timeout=20):
    """One HTTP call. Returns ``(status, body_text)`` and never raises for a status.

    Tests replace this function, which is why the whole GHL flow can be exercised
    without a network or an account.
    """
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # DNS, TLS, timeout
        raise MailError("%s: %s" % (type(exc).__name__, exc))


def _short(text, limit=400):
    text = " ".join(str(text or "").split())
    return text[:limit] + ("..." if len(text) > limit else "")


def _call(method, path, payload=None):
    """Call the GHL API and return the decoded body, or raise MailError."""
    conn = connection()
    if not conn["token"] or not conn["location"]:
        raise MailError(
            "GoHighLevel is not configured: set the API token and location id in "
            "Settings -> Platform email."
        )
    status, raw = _transport(method, conn["base"] + path, _headers(conn), payload)
    try:
        body = json.loads(raw) if raw.strip() else {}
    except ValueError:
        body = {"raw": raw}
    if status >= 400:
        raise MailError(
            "GoHighLevel %s %s failed (%s): %s"
            % (method, path, status, _short(raw or str(body)))
        )
    return body


# ---------------------------------------------------------------------------
# Custom fields
# ---------------------------------------------------------------------------
def custom_fields():
    """The location's contact custom fields, as the UI and the sender record see them."""
    return _catalogue()


def _catalogue():
    """The raw list. Named separately because ``send(custom_fields=...)`` shadows the
    public function for the whole body of ``send``."""
    body = _call(
        "GET", "/locations/%s/customFields?model=contact" % connection()["location"]
    )
    fields = []
    for item in body.get("customFields") or []:
        fields.append(
            {
                "id": item.get("id") or "",
                "name": item.get("name") or "",
                "fieldKey": item.get("fieldKey") or "",
                "dataType": item.get("dataType") or "TEXT",
                "options": item.get("picklistOptions") or [],
            }
        )
    return fields


def _custom_field_values(values, catalogue):
    """Map stored ``{id|fieldKey|name: value}`` onto GHL's ``[{id, value}]``.

    Keys are matched loosely because one account may key a field by id and another by
    ``fieldKey``; anything that matches nothing is skipped rather than sent as a field
    GHL would reject.
    """
    if not values:
        return []
    lookup = {}
    for field in catalogue:
        for key in (field["id"], field["fieldKey"], field["name"]):
            if key:
                lookup[key] = field["id"]
    resolved = []
    for key, value in values.items():
        field_id = lookup.get(key) or lookup.get(str(key).split(".")[-1])
        if field_id and str(value).strip():
            resolved.append({"id": field_id, "value": str(value)})
    return resolved


# ---------------------------------------------------------------------------
# Senders
# ---------------------------------------------------------------------------
def _sender_out(row):
    item = {key: row[key] for key in row.keys()}
    try:
        item["custom_fields"] = json.loads(item.get("custom_fields") or "{}")
    except ValueError:
        item["custom_fields"] = {}
    item["is_default"] = bool(item.get("is_default"))
    return item


def senders(org_id=None):
    """Sender records for one scope. ``org_id=None`` is the platform's own."""
    if org_id is None:
        query = "SELECT * FROM mail_senders WHERE org_id IS NULL"
        params = ()
    else:
        query = "SELECT * FROM mail_senders WHERE org_id = ?"
        params = (org_id,)
    query += " ORDER BY is_default DESC, id ASC"
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_sender_out(row) for row in rows]


def get_sender(sender_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM mail_senders WHERE id = ?", (sender_id,)
        ).fetchone()
    return _sender_out(row) if row else None


def default_sender():
    rows = senders()
    for row in rows:
        if row["is_default"]:
            return row
    return rows[0] if rows else None


def save_sender(data, sender_id=None, org_id=None):
    """Create or update a sender. ``is_default`` clears the others in its scope."""
    data = data or {}
    values = [(data.get(field) or "").strip() for field in TEXT_FIELDS]
    custom = data.get("custom_fields")
    if isinstance(custom, str):
        try:
            custom = json.loads(custom or "{}")
        except ValueError:
            raise MailError("custom_fields must be JSON")
    custom_json = json.dumps(custom or {})
    is_default = 1 if data.get("is_default") else 0

    with get_connection() as conn:
        if sender_id is None:
            cur = conn.execute(
                "INSERT INTO mail_senders (org_id, name, from_name, from_email, reply_to, "
                "signature, logo_url, custom_fields, is_default) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple([org_id] + values + [custom_json, is_default]),
            )
            sender_id = cur.lastrowid
        else:
            conn.execute(
                "UPDATE mail_senders SET name = ?, from_name = ?, from_email = ?, "
                "reply_to = ?, signature = ?, logo_url = ?, custom_fields = ?, "
                "is_default = ?, updated_at = ? WHERE id = ?",
                tuple(values + [custom_json, is_default, now_str(), sender_id]),
            )
    if is_default:
        set_default(sender_id, org_id=org_id)
    return get_sender(sender_id)


def set_default(sender_id, org_id=None):
    """One default per scope - that is what makes ``default_sender`` unambiguous."""
    with get_connection() as conn:
        if org_id is None:
            conn.execute("UPDATE mail_senders SET is_default = 0 WHERE org_id IS NULL")
            conn.execute(
                "UPDATE mail_senders SET is_default = 1 WHERE id = ?", (sender_id,)
            )
        else:
            conn.execute(
                "UPDATE mail_senders SET is_default = 0 WHERE org_id = ?", (org_id,)
            )
            conn.execute(
                "UPDATE mail_senders SET is_default = 1 WHERE id = ? AND org_id = ?",
                (sender_id, org_id),
            )
    return get_sender(sender_id)


def delete_sender(sender_id):
    with get_connection() as conn:
        conn.execute("DELETE FROM mail_senders WHERE id = ?", (sender_id,))
    return True


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------
def upsert_contact(email, name="", custom=None):
    """Create or update the contact and return its id - GHL sends to contacts only."""
    conn = connection()
    payload = {"locationId": conn["location"], "email": email}
    if name:
        payload["name"] = name
    if custom:
        payload["customFields"] = custom
    body = _call("POST", "/contacts/upsert", payload)
    contact = body.get("contact") or {}
    contact_id = contact.get("id") or body.get("id")
    if not contact_id:
        raise MailError(
            "GoHighLevel accepted the contact but returned no id: %s" % _short(body)
        )
    return contact_id


def _plain_text(html):
    """A crude text alternative: GHL shows ``message`` in the conversation thread."""
    import re

    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html or "")
    text = re.sub(r"(?i)<br\s*/?>|</p>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def send(to, subject, html, sender=None, custom_fields=None):
    """Send one email through GHL. Returns the provider's response for the log.

    ``sender`` is a stored sender record (see ``default_sender``); without one the
    from-address falls back to ``GHL_FROM_EMAIL``. GHL only accepts an address that is
    verified in the sub-account, which is why the UI offers a real test send rather
    than a "looks valid" check.
    """
    if not is_configured():
        # Check the connection before the sender: "no from-address" would send the
        # operator looking at the wrong screen when nothing is configured at all.
        raise MailError(
            "GoHighLevel is not configured: set the API token and location id in "
            "Settings -> Platform email."
        )
    sender = sender or default_sender() or {}
    from_email = sender.get("from_email") or settings.get("GHL_FROM_EMAIL")
    if not from_email:
        raise MailError("No from-address: save a sender, or set GHL_FROM_EMAIL in Settings.")

    catalogue = _catalogue()
    merged = dict(sender.get("custom_fields") or {})
    merged.update(custom_fields or {})
    contact_id = upsert_contact(to, "", _custom_field_values(merged, catalogue))

    payload = {
        "type": "Email",
        "contactId": contact_id,
        "subject": subject,
        "html": html,
        "message": _plain_text(html),
        "emailFrom": from_email,
    }
    if sender.get("from_name"):
        payload["emailFrom"] = "%s <%s>" % (sender["from_name"], from_email)
    reply_to = sender.get("reply_to") or settings.get("GHL_REPLY_TO")
    if reply_to:
        payload["emailReplyTo"] = reply_to
    body = _call("POST", "/conversations/messages", payload)
    return {
        "ok": True,
        "contact_id": contact_id,
        "message_id": body.get("messageId") or "",
        "email_message_id": body.get("emailMessageId") or "",
        "conversation_id": body.get("conversationId") or "",
        "from": from_email,
        "to": to,
    }


def test_connection():
    """Prove the token, the version header and the custom-fields scope in one go."""
    try:
        location = _call("GET", "/locations/%s" % connection()["location"])
        fields = custom_fields()
    except MailError as exc:
        return {"ok": False, "output": str(exc)}
    entry = location.get("location") or location
    label = entry.get("name") if isinstance(entry, dict) else None
    return {
        "ok": True,
        "output": "Connected to %s with %d contact custom field(s)."
        % (label or connection()["location"], len(fields)),
    }
