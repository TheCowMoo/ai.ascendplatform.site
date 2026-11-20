"""User administration for both management planes.

The platform owner manages agency staff and every client's people; a client admin
manages only their own workspace. Keeping the rules here means the two screens cannot
drift on the things that matter: a client admin never reaches another org, never creates
an owner, and nobody can remove the last active owner or the last admin of a workspace.
Every successful change is written to ``user_events``.
"""

import json

from werkzeug.security import generate_password_hash

from db import get_connection, now_str

OWNER = "owner"
CLIENT_ADMIN = "client_admin"
MEMBER = "member"
LEGACY_OWNER = "admin"
LEGACY_MEMBER = "client_member"

ROLES = (OWNER, CLIENT_ADMIN, MEMBER)
CLIENT_ROLES = (CLIENT_ADMIN, MEMBER)
STATUSES = ("active", "suspended")
MIN_PASSWORD_LEN = 8

ROLE_LABELS = {
    OWNER: "Agency owner",
    CLIENT_ADMIN: "Client admin",
    MEMBER: "Member",
}


class UserError(Exception):
    """A validation or conflict the caller can fix."""


class UserPermissionError(UserError):
    """The caller is not allowed to touch this account."""


def normalize_role(role):
    role = (role or "").strip()
    if role == LEGACY_OWNER:
        return OWNER
    if role == LEGACY_MEMBER:
        return MEMBER
    return role


def is_owner_role(role):
    return role in (OWNER, LEGACY_OWNER)


def role_label(role):
    return ROLE_LABELS.get(normalize_role(role), (role or "").replace("_", " ").title())


def _as_dict(row):
    return {key: row[key] for key in row.keys()}


def out(row):
    item = _as_dict(row)
    item.pop("password_hash", None)
    role = normalize_role(item.get("role"))
    item["role"] = role
    item["role_label"] = role_label(role)
    item["is_owner"] = is_owner_role(role)
    item["status"] = item.get("status") or "active"
    item["last_login_at"] = item.get("last_login_at")
    item["updated_at"] = item.get("updated_at") or item.get("created_at")
    item["org_name"] = item.get("org_name")
    return item


_USER_SELECT = (
    "SELECT u.id, u.email, u.name, u.role, u.org_id, u.status, u.last_login_at, "
    "u.onboarded_at, u.created_at, u.updated_at, o.name AS org_name "
    "FROM users u LEFT JOIN orgs o ON o.id = u.org_id"
)


def get_user(user_id):
    with get_connection() as conn:
        row = conn.execute(_USER_SELECT + " WHERE u.id = ?", (user_id,)).fetchone()
    return out(row) if row else None


def get_user_with_password(user_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        return None
    item = out(row)
    item["password_hash"] = row["password_hash"]
    return item


def login_user(email):
    """The login row, including the hash, normalised for the role alias."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", ((email or "").strip().lower(),)
        ).fetchone()
    if not row:
        return None
    item = out(row)
    item["password_hash"] = row["password_hash"]
    return item


def session_user(user_id):
    """Reload the account on every authenticated request.

    The session is a hint about *who* is signed in, not a durable copy of their role,
    org or status. Reading the row again makes a role change immediate and makes a
    disable stop the next request rather than the next login.
    """
    if not user_id:
        return None
    return get_user(user_id)


def list_users(org_id=None, q="", role="", status="", limit=500):
    query = _USER_SELECT + " WHERE 1 = 1"
    params = []
    if org_id is not None:
        query += " AND u.org_id = ?"
        params.append(org_id)
    if q:
        needle = "%%%s%%" % (q or "").strip().lower()
        query += " AND (LOWER(u.name) LIKE ? OR LOWER(u.email) LIKE ?)"
        params.extend([needle, needle])
    role = normalize_role(role)
    if role:
        if role == OWNER:
            query += " AND (u.role = ? OR u.role = ?)"
            params.extend([OWNER, LEGACY_OWNER])
        else:
            query += " AND u.role = ?"
            params.append(role)
    if status:
        query += " AND COALESCE(u.status, 'active') = ?"
        params.append(status)
    query += " ORDER BY LOWER(u.name), LOWER(u.email) LIMIT ?"
    params.append(max(1, min(int(limit or 500), 1000)))
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [out(row) for row in rows]


def count_active_owners(exclude_id=None):
    query = (
        "SELECT COUNT(*) FROM users WHERE (role = ? OR role = ?) "
        "AND COALESCE(status, 'active') = 'active'"
    )
    params = [OWNER, LEGACY_OWNER]
    if exclude_id is not None:
        query += " AND id != ?"
        params.append(exclude_id)
    with get_connection() as conn:
        return conn.execute(query, params).fetchone()[0]


def count_active_client_admins(org_id, exclude_id=None):
    if org_id is None:
        return 0
    query = (
        "SELECT COUNT(*) FROM users WHERE role = ? AND org_id = ? "
        "AND COALESCE(status, 'active') = 'active'"
    )
    params = [CLIENT_ADMIN, org_id]
    if exclude_id is not None:
        query += " AND id != ?"
        params.append(exclude_id)
    with get_connection() as conn:
        return conn.execute(query, params).fetchone()[0]


def _audit(actor_id, target_id, org_id, action, detail=None):
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO user_events (actor_id, target_id, org_id, action, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    actor_id,
                    target_id,
                    org_id,
                    action,
                    json.dumps(detail or {}, separators=(",", ":")),
                    now_str(),
                ),
            )
    except Exception:
        # An audit write must not turn a completed account change into a 500 on a
        # database created before the audit table existed.
        pass


def record_event(actor_id, target_id, org_id, action, detail=None):
    """Public audit hook for flows that live outside this module (password reset)."""
    _audit(actor_id, target_id, org_id, action, detail)


def events(org_id=None, target_id=None, limit=100):
    query = (
        "SELECT e.*, a.name AS actor_name, a.email AS actor_email, "
        "t.name AS target_name, t.email AS target_email "
        "FROM user_events e "
        "LEFT JOIN users a ON a.id = e.actor_id "
        "LEFT JOIN users t ON t.id = e.target_id WHERE 1 = 1"
    )
    params = []
    if org_id is not None:
        query += " AND e.org_id = ?"
        params.append(org_id)
    if target_id is not None:
        query += " AND e.target_id = ?"
        params.append(target_id)
    query += " ORDER BY e.id DESC LIMIT ?"
    params.append(max(1, min(int(limit or 100), 500)))
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    result = []
    for row in rows:
        item = _as_dict(row)
        try:
            item["detail"] = json.loads(item.get("detail") or "{}")
        except Exception:
            item["detail"] = {}
        result.append(item)
    return result


def _email_free(email, exclude_id=None):
    query = "SELECT id FROM users WHERE email = ?"
    params = [email]
    if exclude_id is not None:
        query += " AND id != ?"
        params.append(exclude_id)
    with get_connection() as conn:
        return conn.execute(query, params).fetchone() is None


def _clean_email(value):
    email = (value or "").strip().lower()
    if not email or "@" not in email:
        raise UserError("A valid email address is required")
    return email


def _clean_name(value, email):
    return (value or "").strip() or email.split("@")[0]


def _clean_password(value):
    password = value or ""
    if len(password) < MIN_PASSWORD_LEN:
        raise UserError("Password must be at least %d characters" % MIN_PASSWORD_LEN)
    return password


def _parse_org(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise UserError("Client must be a number")


def _client_org_exists(org_id):
    if org_id is None:
        return False
    with get_connection() as conn:
        return conn.execute("SELECT id FROM orgs WHERE id = ?", (org_id,)).fetchone() is not None


def _check_actor_target(target, actor_owner, actor_org):
    if actor_owner:
        return
    if (not actor_org or normalize_role(target["role"]) not in CLIENT_ROLES
            or target["org_id"] != actor_org):
        raise UserPermissionError("You can only manage people in your own client")


def _validate_role(role, actor_owner):
    role = normalize_role(role)
    if role not in ROLES:
        raise UserError("Role must be one of: %s" % ", ".join(ROLES))
    if not actor_owner and role not in CLIENT_ROLES:
        raise UserPermissionError("Client admins cannot assign the agency-owner role")
    return role


def create_user(data, actor_id, actor_owner, actor_org):
    data = data or {}
    email = _clean_email(data.get("email"))
    name = _clean_name(data.get("name"), email)
    password = _clean_password(data.get("password"))
    role = _validate_role(data.get("role") or MEMBER, actor_owner)

    if not actor_owner:
        if not actor_org:
            raise UserPermissionError("Pick your client before adding people")
        requested_org = _parse_org(data.get("org_id"))
        if requested_org not in (None, actor_org):
            raise UserPermissionError("You can only add people to your own client")
        org_id = actor_org
    else:
        org_id = _parse_org(data.get("org_id"))
        if role == OWNER:
            org_id = None

    if role == CLIENT_ADMIN and org_id is None:
        raise UserError("A client admin needs a client")
    if role != OWNER and org_id is not None and not _client_org_exists(org_id):
        raise UserError("That client does not exist")
    if not _email_free(email):
        raise UserError("That email is already in use")

    try:
        with get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO users (email, name, password_hash, role, org_id, status, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'active', ?)",
                (email, name, generate_password_hash(password), role, org_id, now_str()),
            )
            uid = cur.lastrowid
    except Exception as exc:
        raise UserError("Could not create that person (email may already exist): %s" % exc)

    _audit(actor_id, uid, org_id, "create", {"role": role})
    return get_user(uid)


def update_user(user_id, data, actor_id, actor_owner, actor_org):
    data = data or {}
    target = get_user(user_id)
    if not target:
        raise UserError("That person does not exist")
    _check_actor_target(target, actor_owner, actor_org)

    old_role = normalize_role(target["role"])
    old_org = target["org_id"]
    old_status = target.get("status") or "active"

    new_role = old_role
    if "role" in data:
        new_role = _validate_role(data.get("role") or old_role, actor_owner)

    new_status = old_status
    if "status" in data:
        new_status = (data.get("status") or "").strip()
        if new_status not in STATUSES:
            raise UserError("Status must be active or suspended")

    if "org_id" in data:
        if not actor_owner:
            requested_org = _parse_org(data.get("org_id"))
            if requested_org not in (None, old_org):
                raise UserPermissionError("You cannot move a person to another client")
            new_org = old_org
        else:
            new_org = _parse_org(data.get("org_id"))
    else:
        new_org = old_org
    if new_role == OWNER:
        new_org = None
    if new_role == CLIENT_ADMIN and new_org is None:
        raise UserError("A client admin needs a client")

    if new_role != OWNER and new_org is not None and not _client_org_exists(new_org):
        raise UserError("That client does not exist")

    # A change that would remove the final active owner, or the final active admin of
    # a workspace, is refused before anything is written.
    if is_owner_role(old_role) and not (new_role == OWNER and new_status == "active"):
        if count_active_owners(exclude_id=user_id) == 0:
            raise UserError("The platform must keep at least one active agency owner")
    if old_role == CLIENT_ADMIN and old_org is not None:
        losing_admin = (new_role != CLIENT_ADMIN or new_status != "active"
                        or new_org != old_org)
        if losing_admin and count_active_client_admins(old_org, exclude_id=user_id) == 0:
            raise UserError("That client must keep at least one active client admin")

    email = _clean_email(data.get("email")) if "email" in data else target["email"]
    name = _clean_name(data.get("name"), email) if "name" in data else target["name"]
    if email != target["email"] and not _email_free(email, exclude_id=user_id):
        raise UserError("That email is already in use")

    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET email = ?, name = ?, role = ?, org_id = ?, status = ?, "
            "updated_at = ? WHERE id = ?",
            (email, name, new_role, new_org, new_status, now_str(), user_id),
        )

    detail = {}
    for field, before, after in (
        ("email", target["email"], email),
        ("name", target["name"], name),
        ("role", old_role, new_role),
        ("org_id", old_org, new_org),
        ("status", old_status, new_status),
    ):
        if before != after:
            detail[field] = {"from": before, "to": after}
    action = "status" if set(detail) == {"status"} else ("move" if "org_id" in detail else "update")
    _audit(actor_id, user_id, new_org, action, detail)
    return get_user(user_id)


def set_status(user_id, status, actor_id, actor_owner, actor_org):
    if (status or "").strip() not in STATUSES:
        raise UserError("Status must be active or suspended")
    return update_user(user_id, {"status": status}, actor_id, actor_owner, actor_org)


def reset_password(user_id, password, actor_id, actor_owner, actor_org):
    target = get_user(user_id)
    if not target:
        raise UserError("That person does not exist")
    _check_actor_target(target, actor_owner, actor_org)
    password = _clean_password(password)
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (generate_password_hash(password), now_str(), user_id),
        )
    # An admin reset invalidates any reset link the user may still have in their inbox.
    from password_resets import revoke_all
    revoke_all(user_id)
    _audit(actor_id, user_id, target["org_id"], "password", {})
    return target


def delete_user(user_id, actor_id, actor_owner, actor_org):
    target = get_user(user_id)
    if not target:
        raise UserError("That person does not exist")
    if user_id == actor_id:
        raise UserError("You cannot remove your own account")
    _check_actor_target(target, actor_owner, actor_org)

    role = normalize_role(target["role"])
    if is_owner_role(role) and count_active_owners(exclude_id=user_id) == 0:
        raise UserError("The platform must keep at least one active agency owner")
    if (role == CLIENT_ADMIN and target["org_id"] is not None
            and count_active_client_admins(target["org_id"], exclude_id=user_id) == 0):
        raise UserError("That client must keep at least one active client admin")

    _audit(actor_id, user_id, target["org_id"], "delete", {
        "email": target["email"], "role": role,
    })
    with get_connection() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return True
