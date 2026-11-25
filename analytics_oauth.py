"""Google Analytics 4 OAuth for client-owned properties.

The client enters their numeric GA4 property ID, then authorizes read-only access with
their own Google account. The refresh token lives in ``oauth_connections`` keyed by the
client org; a GA4 source points at it with ``credential_ref = "oauth:<id>"``. Existing
service-account sources keep working through :mod:`ga4`.
"""

import datetime
import json
import os
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request

import db
import settings

PROVIDER = "google_analytics"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
STATE_TTL = 600


class OAuthError(Exception):
    """A problem the user or operator can fix."""


def _transport(url, payload=None, headers=None, timeout=20, form=False):
    """POST/GET returning ``(status, body_text)``. Tests replace this."""
    data = None
    if payload is not None:
        if form:
            data = urllib.parse.urlencode(payload).encode("utf-8")
            headers = dict(headers or {})
            headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        else:
            data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers=headers or {},
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def redirect_uri():
    """The exact URI Google must call back. Empty when the platform URL is unknown."""
    explicit = (settings.get("GA4_OAUTH_REDIRECT_URI") or "").strip()
    if explicit:
        return explicit
    base = (settings.get("PLATFORM_URL") or "").strip().rstrip("/")
    if not base:
        domain = (os.environ.get("ADMIN_DOMAIN") or "").strip()
        if domain and domain.lower() not in ("localhost", "127.0.0.1", "::1"):
            base = "https://" + domain
    return (base + "/api/analytics/oauth/google/callback") if base else ""


def configured():
    return bool(
        (settings.get("GA4_OAUTH_CLIENT_ID") or "").strip()
        and (settings.get("GA4_OAUTH_CLIENT_SECRET") or "").strip()
        and redirect_uri()
    )


def missing_config():
    missing = []
    if not (settings.get("GA4_OAUTH_CLIENT_ID") or "").strip():
        missing.append("GA4_OAUTH_CLIENT_ID")
    if not (settings.get("GA4_OAUTH_CLIENT_SECRET") or "").strip():
        missing.append("GA4_OAUTH_CLIENT_SECRET")
    if not redirect_uri():
        missing.append("GA4_OAUTH_REDIRECT_URI or PLATFORM_URL")
    return missing


def normalize_property(value):
    """Accept ``123456789`` or ``properties/123456789`` and return the API form."""
    raw = str(value or "").strip()
    if raw.lower().startswith("properties/"):
        raw = raw.split("/", 1)[1].strip()
    if not re.fullmatch(r"\d{3,}", raw):
        raise OAuthError("Enter the numeric Google Analytics property ID.")
    return "properties/" + raw


def new_state():
    return secrets.token_urlsafe(32)


def authorization_url(state, callback=None):
    if not configured():
        raise OAuthError("Google Analytics sign-in is not configured. Missing: %s."
                         % ", ".join(missing_config()))
    query = urllib.parse.urlencode({
        "client_id": (settings.get("GA4_OAUTH_CLIENT_ID") or "").strip(),
        "redirect_uri": callback or redirect_uri(),
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "select_account consent",
        "state": state,
    })
    return AUTH_URL + "?" + query
def _token_request(payload):
    status, body = _transport(TOKEN_URL, payload, form=True)
    if status >= 400:
        try:
            detail = json.loads(body).get("error_description") or body
        except Exception:
            detail = body
        raise OAuthError("Google rejected the authorization (%s): %s"
                         % (status, str(detail)[:300]))
    try:
        parsed = json.loads(body)
    except ValueError:
        raise OAuthError("Google returned an unreadable authorization response.")
    if not parsed.get("access_token"):
        raise OAuthError("Google did not return an access token.")
    return parsed


def exchange_code(code, callback=None):
    if not (settings.get("GA4_OAUTH_CLIENT_ID") or "").strip() \
            or not (settings.get("GA4_OAUTH_CLIENT_SECRET") or "").strip():
        raise OAuthError("Google Analytics sign-in is not configured.")
    return _token_request({
        "code": code,
        "client_id": (settings.get("GA4_OAUTH_CLIENT_ID") or "").strip(),
        "client_secret": (settings.get("GA4_OAUTH_CLIENT_SECRET") or "").strip(),
        "redirect_uri": callback or redirect_uri(),
        "grant_type": "authorization_code",
    })


def refresh_access_token(refresh_token):
    if not refresh_token:
        raise OAuthError("Google Analytics has no refresh token. Reconnect the account.")
    return _token_request({
        "refresh_token": refresh_token,
        "client_id": (settings.get("GA4_OAUTH_CLIENT_ID") or "").strip(),
        "client_secret": (settings.get("GA4_OAUTH_CLIENT_SECRET") or "").strip(),
        "grant_type": "refresh_token",
    })


def _expires_at(seconds):
    try:
        seconds = max(60, int(seconds or 3600))
    except (TypeError, ValueError):
        seconds = 3600
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")


def _row(row):
    return {key: row[key] for key in row.keys()} if row else None


def get_connection(org_id):
    if org_id is None:
        return None
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM oauth_connections WHERE provider = ? AND org_id = ?",
            (PROVIDER, org_id),
        ).fetchone()
    return _row(row)


def connected(org_id):
    return bool(get_connection(org_id))


def save_connection(org_id, token_payload, account_email=""):
    """Insert/update the client's Google grant. Preserve a refresh token Google omits."""
    if org_id is None:
        raise OAuthError("Choose a client before connecting Google Analytics.")
    existing = get_connection(org_id)
    refresh = (token_payload.get("refresh_token") or "").strip()
    if not refresh and existing:
        refresh = existing.get("refresh_token") or ""
    if not refresh:
        raise OAuthError(
            "Google did not return offline access. Remove this app's access from the "
            "Google Account and try again."
        )
    access = (token_payload.get("access_token") or "").strip()
    if not access and existing:
        access = existing.get("access_token") or ""
    expires = _expires_at(token_payload.get("expires_in")) if access else (
        (existing or {}).get("expires_at"))
    scope = (token_payload.get("scope") or "").strip() or (
        (existing or {}).get("scope") or SCOPE)
    email = (account_email or "").strip() or (
        (existing or {}).get("account_email") or "")
    now = db.now_str()
    with db.get_connection() as conn:
        if existing:
            conn.execute(
                "UPDATE oauth_connections SET account_email = ?, refresh_token = ?, "
                "access_token = ?, expires_at = ?, scope = ?, updated_at = ? WHERE id = ?",
                (email, refresh, access, expires, scope, now, existing["id"]),
            )
        else:
            cur = conn.execute(
                "INSERT INTO oauth_connections (provider, org_id, account_email, "
                "refresh_token, access_token, expires_at, scope, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (PROVIDER, org_id, email, refresh, access, expires, scope, now),
            )
            existing = {"id": cur.lastrowid}
    return get_connection(org_id) or existing

def _fresh(expires_at):
    if not expires_at:
        return False
    try:
        when = datetime.datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return False
    when = when.replace(tzinfo=datetime.timezone.utc)
    return when > datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=60)


def access_token_for_org(org_id):
    connection = get_connection(org_id)
    if not connection:
        raise OAuthError("Google Analytics is not connected. Reconnect the account.")
    token = (connection.get("access_token") or "").strip()
    if token and _fresh(connection.get("expires_at")):
        return token
    refreshed = refresh_access_token(connection.get("refresh_token") or "")
    save_connection(org_id, refreshed)
    updated = get_connection(org_id) or {}
    token = (updated.get("access_token") or "").strip()
    if not token:
        raise OAuthError("Google did not return an access token.")
    return token


def disconnect(org_id):
    with db.get_connection() as conn:
        conn.execute(
            "DELETE FROM oauth_connections WHERE provider = ? AND org_id = ?",
            (PROVIDER, org_id),
        )
    return True


def source_marker(connection_id):
    return "oauth:%s" % connection_id


def is_oauth_source(source):
    return str((source or {}).get("credential_ref") or "").startswith("oauth:")


def connection_id_from_source(source):
    raw = str((source or {}).get("credential_ref") or "")
    if not raw.startswith("oauth:"):
        return None
    try:
        return int(raw.split(":", 1)[1])
    except (TypeError, ValueError):
        return None


def drop_if_unused(org_id, connection_id):
    """Delete a connection once the last GA4 source using it is gone."""
    if connection_id is None:
        return False
    marker = source_marker(connection_id)
    with db.get_connection() as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM data_sources WHERE org_id = ? AND kind = 'ga4' "
            "AND credential_ref = ?", (org_id, marker),
        ).fetchone()[0]
    if remaining:
        return False
    disconnect(org_id)
    return True


