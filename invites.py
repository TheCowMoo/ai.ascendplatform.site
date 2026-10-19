"""Invitations: an expiring, single-use link that lets someone set a password.

The **user row is created at invite time**, with an unusable random password, so email
uniqueness and org scoping are settled before anyone clicks anything. Accepting only
sets the real password - which means a resend is a new token on the same account, and a
withdrawn invite never leaves a half-created user behind.

The token is password-equivalent until it expires, so only its SHA-256 hash is stored
and the plaintext is returned exactly once, to the caller that emails it.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from werkzeug.security import generate_password_hash

import settings
from db import get_connection, now_str

# Whoever is invited joins a client, never the platform: a stray role here would be a
# privilege escalation delivered by email.
ROLES = ("client_admin", "client_member")

# The merge values a template can use, in the order the editor offers them. The label
# and sample are for the console (chips + live preview); the key is what `render`
# replaces. Keeping them here means the UI cannot advertise a token the sender will not
# fill, or miss one it will.
TOKENS = (
    ("name", "Invitee's name", "Alex Morgan"),
    ("org", "Client name", "Acme Ltd"),
    ("role", "Role", "client admin"),
    ("inviter", "Who sent it", "The AscendPlatform team"),
    ("link", "The invitation link", "https://example.com/admin/#/accept/sample-token"),
    ("expires", "Expiry date", "2026-11-20"),
)

# The built-in template: a prebuilt, email-safe layout (tables and inline styles, no
# <style> block - email clients strip it) with every merge value already in place.
# Operators edit this in the console; this copy is what "Reset" restores.
DEFAULT_BODY = """<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f5f6f8;padding:24px 12px;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">
  <tr><td align="center">
    <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:600px;max-width:100%;background:#ffffff;border:1px solid #e6e8ec;border-radius:12px;overflow:hidden">
      <tr>
        <td style="background:#4f46e5;padding:22px 32px;color:#ffffff;font-size:18px;font-weight:600">
          AscendPlatform
        </td>
      </tr>
      <tr>
        <td style="padding:32px">
          <h1 style="margin:0 0 12px;font-size:22px;line-height:1.3;color:#111827">
            You're invited, {{name}}
          </h1>
          <p style="margin:0 0 16px;font-size:15px;line-height:1.6;color:#374151">
            {{inviter}} has invited you to join <strong>{{org}}</strong> as a
            <strong>{{role}}</strong>.
          </p>
          <table role="presentation" cellpadding="0" cellspacing="0" style="margin:24px 0">
            <tr>
              <td style="background:#4f46e5;border-radius:8px">
                <a href="{{link}}" style="display:inline-block;padding:13px 26px;font-size:15px;font-weight:600;color:#ffffff;text-decoration:none">Choose your password</a>
              </td>
            </tr>
          </table>
          <p style="margin:0 0 8px;font-size:13px;line-height:1.6;color:#6b7280">
            This link works once and expires on {{expires}}. If the button does not
            work, copy this address into your browser:
          </p>
          <p style="margin:0 0 20px;font-size:12px;line-height:1.6;word-break:break-all;color:#4f46e5">{{link}}</p>
          <p style="margin:0;font-size:13px;line-height:1.6;color:#6b7280">
            If you were not expecting this invitation you can ignore this email - no
            account becomes active until that link is used.
          </p>
        </td>
      </tr>
      <tr>
        <td style="padding:18px 32px;background:#f9fafb;border-top:1px solid #e6e8ec;font-size:12px;line-height:1.5;color:#9ca3af">
          Sent by AscendPlatform.
        </td>
      </tr>
    </table>
  </td></tr>
</table>"""


def token_samples():
    """``{key: sample}`` - what the console uses to preview an unsent template."""
    return {key: sample for key, _label, sample in TOKENS}



class InviteError(Exception):
    """Anything the operator or the invitee can fix, phrased for them."""


def _token_hash(token):
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _deadline():
    days = max(1, settings.get_int("INVITE_EXPIRES_DAYS", 7))
    moment = datetime.now(timezone.utc) + timedelta(days=days)
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _status(row):
    """pending | accepted | revoked | expired - derived, never stored.

    A status column would need a writer to keep it true; deriving it from the
    timestamps means an invitation cannot read as "pending" after it has expired.
    """
    if row["accepted_at"]:
        return "accepted"
    if row["revoked_at"]:
        return "revoked"
    if (row["expires_at"] or "") and row["expires_at"] < now_str():
        return "expired"
    return "pending"


def out(row):
    item = {key: row[key] for key in row.keys()}
    item["status"] = _status(row)
    return item


def list_invites(org_id=None, limit=200):
    query = "SELECT * FROM invites WHERE 1 = 1"
    params = []
    if org_id is not None:
        query += " AND org_id = ?"
        params.append(org_id)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, int(limit)))
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [out(row) for row in rows]


def get_invite(invite_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM invites WHERE id = ?", (invite_id,)).fetchone()
    return out(row) if row else None


def lookup(token):
    if not (token or "").strip():
        return None
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM invites WHERE token_hash = ?", (_token_hash(token),)
        ).fetchone()
    return out(row) if row else None


def create(email, name="", org_id=None, role="client_admin", invited_by=None):
    """Invite someone, creating their (locked) account if it does not exist yet.

    Returns ``(invite, token)`` - the token is plaintext here and nowhere else.
    """
    email = (email or "").strip().lower()
    name = (name or "").strip()
    role = (role or "client_admin").strip()
    if not email or "@" not in email:
        raise InviteError("A valid email address is required")
    if role not in ROLES:
        raise InviteError("role must be one of: %s" % ", ".join(ROLES))

    with get_connection() as conn:
        existing = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if existing is None:
            # Unusable on purpose: the account exists so the email is unique and the
            # org is fixed, but nobody knows this password until the invite is accepted.
            cur = conn.execute(
                "INSERT INTO users (email, name, password_hash, role, org_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    email,
                    name or email.split("@")[0],
                    generate_password_hash(secrets.token_urlsafe(32)),
                    role,
                    org_id,
                ),
            )
            user_id = cur.lastrowid
        else:
            if (existing["org_id"] or None) != (org_id or None):
                raise InviteError(
                    "That email already belongs to another client. Remove them there first."
                )
            user_id = existing["id"]
            name = name or existing["name"]

        # One live link per address: an older one is withdrawn rather than left usable.
        conn.execute(
            "UPDATE invites SET revoked_at = ? WHERE email = ? "
            "AND accepted_at IS NULL AND revoked_at IS NULL",
            (now_str(), email),
        )
        token = secrets.token_urlsafe(32)
        cur = conn.execute(
            "INSERT INTO invites (email, name, org_id, role, user_id, token_hash, "
            "invited_by, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (email, name, org_id, role, user_id, _token_hash(token), invited_by, _deadline()),
        )
        invite_id = cur.lastrowid
    return get_invite(invite_id), token


def resend(invite_id):
    """A new token and a fresh deadline for an invitation already on the list."""
    invite = get_invite(invite_id)
    if not invite:
        raise InviteError("That invitation does not exist")
    if invite["status"] == "accepted":
        raise InviteError("That invitation has already been accepted")
    token = secrets.token_urlsafe(32)
    with get_connection() as conn:
        conn.execute(
            "UPDATE invites SET token_hash = ?, expires_at = ?, revoked_at = NULL "
            "WHERE id = ?",
            (_token_hash(token), _deadline(), invite_id),
        )
    return get_invite(invite_id), token


def revoke(invite_id):
    with get_connection() as conn:
        conn.execute(
            "UPDATE invites SET revoked_at = ? WHERE id = ? AND accepted_at IS NULL",
            (now_str(), invite_id),
        )
    return get_invite(invite_id)


def accept(token, password):
    """Set the password for the invited account and spend the token.

    The password rule itself is applied by the caller (``app._check_new_password``) so
    there is one definition of it; this only refuses an empty one.
    """
    if not (password or "").strip():
        raise InviteError("A password is required.")
    invite = lookup(token)
    if not invite:
        raise InviteError("That invitation link is not valid.")
    if invite["status"] == "accepted":
        raise InviteError("That invitation has already been used.")
    if invite["status"] == "revoked":
        raise InviteError("That invitation was withdrawn - ask for a new one.")
    if invite["status"] == "expired":
        raise InviteError("That invitation has expired - ask for a new one.")
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(password), invite["user_id"]),
        )
        conn.execute(
            "UPDATE invites SET accepted_at = ? WHERE id = ?", (now_str(), invite["id"])
        )
        user = conn.execute(
            "SELECT id, email, name, role, org_id FROM users WHERE id = ?",
            (invite["user_id"],),
        ).fetchone()
    if user is None:
        raise InviteError("The account behind that invitation no longer exists.")
    return {key: user[key] for key in user.keys()}


def preview(token):
    """What the accept page may show before anyone signs in - never the token itself."""
    invite = lookup(token)
    if not invite:
        return None
    org_name = None
    if invite.get("org_id"):
        with get_connection() as conn:
            row = conn.execute(
                "SELECT name FROM orgs WHERE id = ?", (invite["org_id"],)
            ).fetchone()
        org_name = row["name"] if row else None
    return {
        "email": invite["email"],
        "name": invite["name"],
        "org": org_name,
        "role": invite["role"],
        "status": invite["status"],
        "expires_at": invite["expires_at"],
    }


def render(invite, token, base_url="", inviter=""):
    """The invitation as ``(subject, html)``, filling the configured template.

    ``base_url`` comes from the request that sends it, so the link points at whatever
    host the operator is actually using rather than a hardcoded domain.
    """
    base = (base_url or settings.get("PLATFORM_URL") or "").rstrip("/")
    link = "%s/admin/#/accept/%s" % (base, token) if base else "/admin/#/accept/%s" % token
    subject = settings.get("INVITE_SUBJECT") or "You're invited to AscendPlatform"
    body = settings.get("INVITE_BODY_HTML") or DEFAULT_BODY
    org_name = ""
    if invite.get("org_id"):
        with get_connection() as conn:
            row = conn.execute(
                "SELECT name FROM orgs WHERE id = ?", (invite["org_id"],)
            ).fetchone()
        org_name = row["name"] if row else ""
    values = {
        "name": invite.get("name") or invite.get("email") or "there",
        "org": org_name or "AscendPlatform",
        "role": invite.get("role") or "",
        "link": link,
        "expires": (invite.get("expires_at") or "")[:10],
        "inviter": inviter or "The AscendPlatform team",
    }
    for key, value in values.items():
        marker = "{{%s}}" % key
        subject = subject.replace(marker, str(value))
        body = body.replace(marker, str(value))
    return subject, body
