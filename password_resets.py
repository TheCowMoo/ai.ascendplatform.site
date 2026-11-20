"""Short-lived, single-use password reset links.

The token is password-equivalent until it expires, so only its SHA-256 hash is stored.
A request never reveals whether an email belongs to an account; the caller always shows
the same message. The platform's existing GoHighLevel sender carries the link, and a
successful reset also revokes every other outstanding reset for that user.
"""

import hashlib
import html
import secrets
from datetime import datetime, timedelta, timezone

from werkzeug.security import generate_password_hash

import settings
import user_service
from db import get_connection, now_str

TOKEN_TTL_MINUTES = 60
THROTTLE_SECONDS = 60
MIN_PASSWORD_LEN = 8


class ResetError(Exception):
    """Anything the person resetting a password can fix."""


def _token_hash(token):
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _deadline():
    moment = datetime.now(timezone.utc) + timedelta(minutes=TOKEN_TTL_MINUTES)
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _throttle_cutoff():
    moment = datetime.now(timezone.utc) - timedelta(seconds=THROTTLE_SECONDS)
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def request(email):
    """Create a reset token for an active account, or return ``None``.

    ``None`` is deliberately indistinguishable from "account not found" at the HTTP
    layer: revealing the difference would turn this into an account-enumeration API.
    """
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return None
    with get_connection() as conn:
        user = conn.execute(
            "SELECT id, email, name, role, org_id, status FROM users WHERE email = ?",
            (email,),
        ).fetchone()
        if not user or (user["status"] or "active") != "active":
            return None

        # A rapid second click must not spray mail. The generic response still applies.
        recent = conn.execute(
            "SELECT id FROM password_resets WHERE user_id = ? AND requested_at > ?",
            (user["id"], _throttle_cutoff()),
        ).fetchone()
        if recent:
            return None

        # Only the newest link may work. `used_at` means "spent or withdrawn".
        conn.execute(
            "UPDATE password_resets SET used_at = ? WHERE user_id = ? AND used_at IS NULL",
            (now_str(), user["id"]),
        )
        token = secrets.token_urlsafe(32)
        conn.execute(
            "INSERT INTO password_resets "
            "(user_id, email, token_hash, requested_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user["id"], user["email"], _token_hash(token), now_str(), _deadline()),
        )
    return token, {key: user[key] for key in user.keys()}


def get(token):
    """The reset row plus account context, with a derived status."""
    if not (token or "").strip():
        return None
    with get_connection() as conn:
        row = conn.execute(
            "SELECT r.*, u.name AS user_name, u.role, u.org_id, u.status AS user_status "
            "FROM password_resets r LEFT JOIN users u ON u.id = r.user_id "
            "WHERE r.token_hash = ?",
            (_token_hash(token),),
        ).fetchone()
    if not row:
        return None
    item = {key: row[key] for key in row.keys()}
    if row["used_at"]:
        item["status"] = "used"
    elif (row["expires_at"] or "") < now_str():
        item["status"] = "expired"
    elif (row["user_status"] or "active") != "active":
        item["status"] = "inactive"
    else:
        item["status"] = "pending"
    item["email"] = row["email"]
    item["name"] = row["user_name"] or ""
    return item


def consume(token, password):
    """Set the new password and spend this token and every other reset for the user."""
    password = password or ""
    if len(password) < MIN_PASSWORD_LEN:
        raise ResetError("Use at least %d characters." % MIN_PASSWORD_LEN)
    reset = get(token)
    if not reset or reset["status"] != "pending":
        raise ResetError("This reset link is invalid or has expired. Request a new one.")

    with get_connection() as conn:
        # Claim this token atomically. Two people opening the same link at once must
        # not both change the password; the loser gets the normal "expired" message.
        claimed = conn.execute(
            "UPDATE password_resets SET used_at = ? WHERE id = ? AND used_at IS NULL",
            (now_str(), reset["id"]),
        )
        if claimed.rowcount != 1:
            raise ResetError("This reset link is invalid or has expired. Request a new one.")
        conn.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (generate_password_hash(password), now_str(), reset["user_id"]),
        )
        conn.execute(
            "UPDATE password_resets SET used_at = ? "
            "WHERE user_id = ? AND used_at IS NULL",
            (now_str(), reset["user_id"]),
        )
        user = conn.execute(
            "SELECT id, email, name, role, org_id, status FROM users WHERE id = ?",
            (reset["user_id"],),
        ).fetchone()
    if not user:
        raise ResetError("That account no longer exists.")
    result = {key: user[key] for key in user.keys()}
    user_service.record_event(
        None, result["id"], result["org_id"], "password_reset",
        {"method": "email", "email": result["email"]},
    )
    return result


def revoke_all(user_id):
    """Invalidate outstanding reset links after a password change elsewhere."""
    if not user_id:
        return
    with get_connection() as conn:
        conn.execute(
            "UPDATE password_resets SET used_at = ? "
            "WHERE user_id = ? AND used_at IS NULL",
            (now_str(), user_id),
        )


def render(user, token, base_url=""):
    """The reset email. Built here so every link uses the same public path."""
    brand = settings.get("BRAND_NAME") or "AscendPlatform"
    base = (base_url or settings.get("PLATFORM_URL") or "").rstrip("/")
    link = ("%s/admin/#/reset/%s" % (base, token)
            if base else "/admin/#/reset/%s" % token)
    name = html.escape(user.get("name") or user.get("email") or "there")
    safe_brand = html.escape(brand)
    safe_link = html.escape(link, quote=True)
    subject = "Reset your %s password" % brand
    body = """<table role="presentation" width="100%%" cellpadding="0" cellspacing="0"
 style="background:#f5f6f8;padding:24px 12px;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">
  <tr><td align="center">
    <table role="presentation" width="600" cellpadding="0" cellspacing="0"
      style="width:600px;max-width:100%%;background:#ffffff;border:1px solid #e6e8ec;border-radius:12px;overflow:hidden">
      <tr><td style="background:#4f46e5;padding:22px 32px;color:#ffffff;font-size:18px;font-weight:600">%s</td></tr>
      <tr><td style="padding:32px">
        <h1 style="margin:0 0 12px;font-size:22px;line-height:1.3;color:#111827">Reset your password</h1>
        <p style="margin:0 0 16px;font-size:15px;line-height:1.6;color:#374151">Hi %s,</p>
        <p style="margin:0 0 22px;font-size:15px;line-height:1.6;color:#374151">
          We received a request to reset the password for your %s account. Choose a new
          password using the button below. This link works once and expires in %d minutes.
        </p>
        <table role="presentation" cellpadding="0" cellspacing="0" style="margin:24px 0">
          <tr><td style="background:#4f46e5;border-radius:8px">
            <a href="%s" style="display:inline-block;padding:13px 26px;font-size:15px;font-weight:600;color:#ffffff;text-decoration:none">Choose a new password</a>
          </td></tr>
        </table>
        <p style="margin:0 0 8px;font-size:13px;line-height:1.6;color:#6b7280">
          If the button does not work, copy this address into your browser:
        </p>
        <p style="margin:0 0 20px;font-size:12px;line-height:1.6;word-break:break-all;color:#4f46e5">%s</p>
        <p style="margin:0;font-size:13px;line-height:1.6;color:#6b7280">
          If you did not request this, you can ignore this email. Your password has not changed.
        </p>
      </td></tr>
    </table>
  </td></tr>
</table>""" % (safe_brand, name, safe_brand, TOKEN_TTL_MINUTES, safe_link, safe_link)
    return subject, body
