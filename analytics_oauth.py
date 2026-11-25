"""Google connections for GA4, Tag Manager and BigQuery discovery.

One grant can serve a client (``connection_scope='client'``) or the agency
(``connection_scope='agency'`` at org 0, usable for every client the operator chooses).
Tokens are encrypted at rest; the legacy plaintext columns are blanked on the next save.

Read scopes are requested first. A write upgrade is a second, explicit consent; it is
never silently added to an existing grant.
"""

import base64
import datetime
import hashlib
import json
import os
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request

import db
import settings

PROVIDER = "google"
LEGACY_PROVIDER = "google_analytics"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
STATE_TTL = 600

SCOPE_ANALYTICS_READONLY = "https://www.googleapis.com/auth/analytics.readonly"
SCOPE_ANALYTICS_EDIT = "https://www.googleapis.com/auth/analytics.edit"
SCOPE_TAGMANAGER_READONLY = "https://www.googleapis.com/auth/tagmanager.readonly"
SCOPE_TAGMANAGER_EDIT_CONTAINERS = "https://www.googleapis.com/auth/tagmanager.edit.containers"
SCOPE_TAGMANAGER_EDIT_VERSIONS = (
    "https://www.googleapis.com/auth/tagmanager.edit.containerversions"
)
SCOPE_TAGMANAGER_PUBLISH = "https://www.googleapis.com/auth/tagmanager.publish"

READ_SCOPES = (SCOPE_ANALYTICS_READONLY, SCOPE_TAGMANAGER_READONLY)
WRITE_SCOPES = (
    SCOPE_ANALYTICS_EDIT,
    SCOPE_TAGMANAGER_EDIT_CONTAINERS,
    SCOPE_TAGMANAGER_EDIT_VERSIONS,
    SCOPE_TAGMANAGER_PUBLISH,
)
# Kept for callers that only know the original single-scope constant.
SCOPE = SCOPE_ANALYTICS_READONLY


def _key_material():
    raw = (os.environ.get("OAUTH_TOKEN_KEY") or os.environ.get("SECRET_KEY") or "").strip()
    if not raw:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".secret_key")
        try:
            with open(path, "r", encoding="utf-8") as handle:
                raw = handle.read().strip()
        except OSError:
            raw = ""
    if not raw:
        raise OAuthError(
            "Token encryption is not configured. Set OAUTH_TOKEN_KEY or SECRET_KEY."
        )
    return base64.urlsafe_b64encode(hashlib.sha256(raw.encode("utf-8")).digest())


def _fernet():
    from cryptography.fernet import Fernet

    return Fernet(_key_material())


def _encrypt(value):
    text = str(value or "").strip()
    if not text:
        return ""
    return _fernet().encrypt(text.encode("utf-8")).decode("ascii")


def _decrypt(value, fallback=""):
    text = str(value or "").strip()
    if not text:
        return str(fallback or "")
    try:
        return _fernet().decrypt(text.encode("ascii")).decode("utf-8")
    except Exception:
        raise OAuthError(
            "The stored Google token could not be decrypted. Reconnect the account."
        )


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


def scopes_for(write=False):
    return list(READ_SCOPES + (WRITE_SCOPES if write else ()))


def granted_scopes(connection):
    return [scope for scope in str((connection or {}).get("scope") or "").split()
            if scope]


def has_scope(connection, scope):
    return scope in granted_scopes(connection)


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


def authorization_url(state, callback=None, write=False):
    if not configured():
        raise OAuthError("Google sign-in is not configured. Missing: %s."
                         % ", ".join(missing_config()))
    query = urllib.parse.urlencode({
        "client_id": (settings.get("GA4_OAUTH_CLIENT_ID") or "").strip(),
        "redirect_uri": callback or redirect_uri(),
        "response_type": "code",
        "scope": " ".join(scopes_for(write)),
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
        raise OAuthError("Google sign-in is not configured.")
    return _token_request({
        "code": code,
        "client_id": (settings.get("GA4_OAUTH_CLIENT_ID") or "").strip(),
        "client_secret": (settings.get("GA4_OAUTH_CLIENT_SECRET") or "").strip(),
        "redirect_uri": callback or redirect_uri(),
        "grant_type": "authorization_code",
    })


def refresh_access_token(refresh_token):
    if not refresh_token:
        raise OAuthError("Google has no refresh token. Reconnect the account.")
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
    if not row:
        return None
    out = {key: row[key] for key in row.keys()}
    legacy_refresh = out.pop("refresh_token", "") or ""
    legacy_access = out.pop("access_token", "") or ""
    out["refresh_token"] = _decrypt(out.pop("refresh_token_enc", ""), legacy_refresh)
    out["access_token"] = _decrypt(out.pop("access_token_enc", ""), legacy_access)
    out["connection_scope"] = out.get("connection_scope") or "client"
    return out


def _select(org_id):
    with db.get_connection() as conn:
        return conn.execute(
            "SELECT * FROM oauth_connections WHERE org_id = ? "
            "AND provider IN (?, ?) ORDER BY id DESC LIMIT 1",
            (org_id, PROVIDER, LEGACY_PROVIDER),
        ).fetchone()


def get_connection(org_id, scope="client"):
    """One client's grant, or the agency grant when ``scope='agency'``.

    The agency row lives at org 0, the platform's own org. A client never receives that
    row: callers that need it must be owner-guarded.
    """
    if scope == "agency":
        org_id = db.PLATFORM_ORG
    elif org_id is None:
        return None
    return _row(_select(org_id))


def get_agency_connection():
    return get_connection(db.PLATFORM_ORG, scope="agency")


def get_connection_by_id(connection_id):
    if connection_id is None:
        return None
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM oauth_connections WHERE id = ?", (connection_id,)
        ).fetchone()
    return _row(row)


def connected(org_id, scope="client"):
    if scope == "agency":
        return bool(get_agency_connection())
    return bool(get_connection(org_id, "client") or get_agency_connection())


def save_connection(org_id, token_payload, account_email="", connection_scope="client"):
    """Insert/update the grant, preserving a refresh token Google omits."""
    if connection_scope == "agency":
        org_id = db.PLATFORM_ORG
    elif org_id is None:
        raise OAuthError("Choose a client before connecting Google.")
    existing = get_connection(org_id, scope=connection_scope)
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
        (existing or {}).get("scope") or " ".join(READ_SCOPES))
    email = (account_email or "").strip() or (
        (existing or {}).get("account_email") or "")
    now = db.now_str()
    refresh_enc = _encrypt(refresh)
    access_enc = _encrypt(access)
    with db.get_connection() as conn:
        if existing:
            conn.execute(
                "UPDATE oauth_connections SET provider = ?, connection_scope = ?, "
                "account_email = ?, refresh_token = '', access_token = '', "
                "refresh_token_enc = ?, access_token_enc = ?, expires_at = ?, "
                "scope = ?, updated_at = ? WHERE id = ?",
                (PROVIDER, connection_scope, email, refresh_enc, access_enc, expires,
                 scope, now, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO oauth_connections (provider, org_id, connection_scope, "
                "account_email, refresh_token, access_token, refresh_token_enc, "
                "access_token_enc, expires_at, scope, updated_at) "
                "VALUES (?, ?, ?, ?, '', '', ?, ?, ?, ?, ?)",
                (PROVIDER, org_id, connection_scope, email, refresh_enc, access_enc,
                 expires, scope, now),
            )
    return get_connection(org_id, scope=connection_scope)


def encrypt_legacy_tokens():
    """Encrypt any older plaintext rows once. Safe to call on every startup."""
    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT id, refresh_token, access_token FROM oauth_connections "
            "WHERE (refresh_token != '' OR access_token != '') "
            "AND refresh_token_enc = '' AND access_token_enc = ''"
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE oauth_connections SET refresh_token = '', access_token = '', "
                "refresh_token_enc = ?, access_token_enc = ? WHERE id = ?",
                (_encrypt(row["refresh_token"]), _encrypt(row["access_token"]), row["id"]),
            )
    return len(rows)


def _fresh(expires_at):
    if not expires_at:
        return False
    try:
        when = datetime.datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return False
    when = when.replace(tzinfo=datetime.timezone.utc)
    return when > datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=60)


def access_token_for_connection(connection):
    if not connection:
        raise OAuthError("Google is not connected. Reconnect the account.")
    token = (connection.get("access_token") or "").strip()
    if token and _fresh(connection.get("expires_at")):
        return token
    refreshed = refresh_access_token(connection.get("refresh_token") or "")
    save_connection(connection.get("org_id"), refreshed,
                    account_email=connection.get("account_email") or "",
                    connection_scope=connection.get("connection_scope") or "client")
    updated = get_connection(connection.get("org_id"),
                             scope=connection.get("connection_scope") or "client") or {}
    token = (updated.get("access_token") or "").strip()
    if not token:
        raise OAuthError("Google did not return an access token.")
    return token


def access_token_for_org(org_id, scope="client"):
    connection = get_connection(org_id, scope=scope)
    if not connection and scope == "client":
        connection = get_agency_connection()
    if not connection:
        raise OAuthError("Google is not connected. Reconnect the account.")
    return access_token_for_connection(connection)


def access_token_for_source(source):
    """The token that owns this source, whether client- or agency-scoped."""
    connection_id = connection_id_from_source(source or {})
    connection = get_connection_by_id(connection_id) if connection_id else None
    if connection and connection.get("connection_scope") != "agency" \
            and connection.get("org_id") != (source or {}).get("org_id"):
        connection = None
    if not connection:
        connection = get_connection((source or {}).get("org_id"), "client") \
            or get_agency_connection()
    if not connection:
        raise OAuthError("Google is not connected. Reconnect the account.")
    return access_token_for_connection(connection)


def disconnect(org_id, scope="client"):
    if scope == "agency":
        org_id = db.PLATFORM_ORG
    with db.get_connection() as conn:
        conn.execute(
            "DELETE FROM oauth_connections WHERE org_id = ? AND provider IN (?, ?)",
            (org_id, PROVIDER, LEGACY_PROVIDER),
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
    """Delete a connection once the last GA4 source using it is gone.

    An agency connection is shared across clients, so its usage is counted across every
    org. A client connection is counted inside that client only.
    """
    if connection_id is None:
        return False
    marker = source_marker(connection_id)
    connection = get_connection_by_id(connection_id)
    scope = (connection or {}).get("connection_scope") or "client"
    with db.get_connection() as conn:
        if scope == "agency":
            remaining = conn.execute(
                "SELECT COUNT(*) FROM data_sources WHERE kind = 'ga4' "
                "AND credential_ref = ?", (marker,),
            ).fetchone()[0]
        else:
            remaining = conn.execute(
                "SELECT COUNT(*) FROM data_sources WHERE org_id = ? AND kind = 'ga4' "
                "AND credential_ref = ?", (org_id, marker),
            ).fetchone()[0]
    if remaining:
        return False
    disconnect(org_id, scope=scope)
    return True


