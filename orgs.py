"""
Client tenants (stage S7).

An org is a paying client. The platform operator is *not* an org: the ``owner``
role sees every row and bypasses scoping, while ``client_admin`` and ``member``
see only their own org's data.

``users.org_id`` is what makes a login belong to a client, and it decides what that
login can read. It is never taken from request input - only from the signed session
(``app.current_org``).
"""

import re

from db import get_connection, now_str

DEFAULT_PLAN = "standard"
DEFAULT_STATUS = "active"
PLANS = ("trial", "standard", "pro", "enterprise")
STATUSES = ("active", "suspended", "cancelled")


class OrgError(Exception):
    """Raised for invalid input, or a slug that is already taken."""


def _as_dict(row):
    return {k: row[k] for k in row.keys()}


def slugify(value):
    """A URL/identifier-safe slug: lowercase, dashes, no punctuation."""
    text = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower())
    return text.strip("-")[:60]


def org_out(row):
    out = _as_dict(row)
    out["notes"] = out.get("notes") or ""
    return out


def list_orgs(limit=200):
    """Every client, newest first."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM orgs ORDER BY id DESC LIMIT %d" % max(1, int(limit))
        ).fetchall()
    return [org_out(row) for row in rows]


def get_org(org_id):
    if not org_id:
        return None
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM orgs WHERE id = ?", (org_id,)).fetchone()
    return org_out(row) if row else None


def get_org_by_slug(slug):
    value = (slug or "").strip().lower()
    if not value:
        return None
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM orgs WHERE slug = ?", (value,)).fetchone()
    return org_out(row) if row else None


def count_orgs():
    with get_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM orgs").fetchone()[0]


def _clean(name, plan, status):
    name = (name or "").strip()
    if not name:
        raise OrgError("A client name is required")
    if plan and plan not in PLANS:
        raise OrgError("Plan must be one of: %s" % ", ".join(PLANS))
    if status and status not in STATUSES:
        raise OrgError("Status must be one of: %s" % ", ".join(STATUSES))
    return name


def _check_email_free(email):
    with get_connection() as conn:
        return not conn.execute(
            "SELECT id FROM users WHERE email = ?", (email,)
        ).fetchone()


def create_org(data):
    """Create a client, optionally with its first login.

    An org with no user would be unreachable, so when an email is supplied the first
    ``client_admin`` is created in the same call and attached to it.
    """
    data = data or {}
    name = _clean(data.get("name"), data.get("plan"), data.get("status"))
    slug = slugify(data.get("slug") or name)
    if not slug:
        raise OrgError("Could not derive a slug from %r" % name)
    if get_org_by_slug(slug):
        raise OrgError("A client with the slug %r already exists" % slug)

    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if email and not password:
        raise OrgError("Give the first user a password, or omit the email too")
    if email and not _check_email_free(email):
        raise OrgError("That email is already in use")

    from werkzeug.security import generate_password_hash

    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO orgs (name, slug, plan, status, notes) VALUES (?, ?, ?, ?, ?)",
            (
                name,
                slug,
                data.get("plan") or DEFAULT_PLAN,
                data.get("status") or DEFAULT_STATUS,
                data.get("notes") or "",
            ),
        )
        org_id = cur.lastrowid
        if email:
            conn.execute(
                "INSERT INTO users (email, name, password_hash, role, org_id) "
                "VALUES (?, ?, ?, 'client_admin', ?)",
                (
                    email,
                    data.get("contact_name") or name,
                    generate_password_hash(password),
                    org_id,
                ),
            )
    return get_org(org_id)


def update_org(org_id, data):
    """Patch a client. Only the keys present in ``data`` are touched."""
    existing = get_org(org_id)
    if not existing:
        return None
    data = data or {}
    plan = data.get("plan", existing["plan"])
    status = data.get("status", existing["status"])
    name = _clean(data.get("name", existing["name"]), plan, status)

    fields = {
        "name": name,
        "plan": plan,
        "status": status,
        "notes": data.get("notes", existing["notes"]),
        "updated_at": now_str(),
    }
    assignments = ", ".join("%s = ?" % key for key in fields)
    with get_connection() as conn:
        conn.execute(
            "UPDATE orgs SET %s WHERE id = ?" % assignments,
            tuple(fields.values()) + (org_id,),
        )
    return get_org(org_id)


def delete_org(org_id):
    """Remove a client. Refuses while it still has users, so nothing is orphaned."""
    if not get_org(org_id):
        return False
    with get_connection() as conn:
        users = conn.execute(
            "SELECT COUNT(*) FROM users WHERE org_id = ?", (org_id,)
        ).fetchone()[0]
    if users:
        raise OrgError(
            "This client still has %d user(s). Move or delete them first." % users
        )
    with get_connection() as conn:
        conn.execute("DELETE FROM orgs WHERE id = ?", (org_id,))
    return True


def list_org_users(org_id):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, email, name, role, org_id FROM users WHERE org_id = ? ORDER BY id",
            (org_id,),
        ).fetchall()
    return [_as_dict(row) for row in rows]


def add_org_user(org_id, data):
    """Add a login to a client - used by the platform console and the Team tab."""
    if not get_org(org_id):
        raise OrgError("No such client")
    data = data or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not email or not password:
        raise OrgError("Email and password are required")
    role = data.get("role") or "member"
    if role not in ("client_admin", "member"):
        raise OrgError("Role must be client_admin or member")
    if not _check_email_free(email):
        raise OrgError("That email is already in use")

    from werkzeug.security import generate_password_hash

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO users (email, name, password_hash, role, org_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                email,
                data.get("name") or email,
                generate_password_hash(password),
                role,
                org_id,
            ),
        )
    return list_org_users(org_id)
