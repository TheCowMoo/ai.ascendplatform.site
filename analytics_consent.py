"""The explicit consent behind one-click Analytics setup.

Google's OAuth grant says the platform *may* call Analytics and Tag Manager. It does not
say the person understood that setup is recurring and that Google's own terms, privacy
obligations and Cloud costs remain theirs. This module keeps that acknowledgement
versioned and revocable; the setup route refuses to write without an active grant.
"""

import json

import db
import settings

KIND = "setup_automation"
# Bump this whenever DISCLAIMER changes. An older row stops counting as active.
VERSION = "2026-11-26"

DISCLAIMER = (
    "I am authorised to bind this workspace and its Google account to Analytics setup.",
    "AscendPlatform may create or change GA4 properties, data streams, key events, "
    "audiences, Tag Manager containers, tags, triggers and published versions, install "
    "the Tag Manager snippet on this workspace's published pages and forms, and create a "
    "BigQuery export link when I have configured a Google Cloud project. It may repeat "
    "these actions when the workspace needs them, not only once.",
    "I remain responsible for accepting Google Analytics, Tag Manager and Google Cloud "
    "terms, and for the Google account itself. This consent is not a substitute for "
    "Google's terms.",
    "I am responsible for privacy notices, cookie consent, data collection, and any "
    "Google Cloud or BigQuery charges. AscendPlatform does not create a Google Cloud "
    "project or a Tag Manager account for me.",
    "Google may still require me to act in its interface for account creation, OAuth "
    "verification or quota approval. Those limits are outside AscendPlatform's control.",
    "I can revoke future automation on the Analytics page at any time. Revocation stops "
    "future actions but does not undo resources already created or delete data already "
    "collected.",
)


class ConsentError(Exception):
    """A consent problem the person can fix."""


def _out(row):
    if not row:
        return None
    item = {key: row[key] for key in row.keys()}
    try:
        item["scopes"] = json.loads(item.get("scopes") or "[]")
    except (TypeError, ValueError):
        item["scopes"] = []
    try:
        item["metadata"] = json.loads(item.get("metadata") or "{}")
    except (TypeError, ValueError):
        item["metadata"] = {}
    return item


def latest(org_id, kind=KIND):
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM analytics_consents WHERE org_id = ? AND kind = ? "
            "ORDER BY id DESC LIMIT 1", (org_id, kind),
        ).fetchone()
    return _out(row)


def status(org_id, kind=KIND):
    row = latest(org_id, kind)
    active = bool(
        row and not (row.get("revoked_at") or "").strip()
        and row.get("version") == VERSION
    )
    return {
        "accepted": active,
        "kind": kind,
        "version": VERSION,
        "accepted_version": (row or {}).get("version") or "",
        "accepted_at": (row or {}).get("accepted_at") or "",
        "revoked_at": (row or {}).get("revoked_at") or "",
        "scopes": (row or {}).get("scopes") or [],
        "disclaimer": list(DISCLAIMER),
    }


def active(org_id, kind=KIND):
    return status(org_id, kind)["accepted"]


def grant(org_id, user_id, actor="", scopes=None, metadata=None, kind=KIND):
    """Record an active consent and switch client autonomy on.

    The settings write is intentional: the person granting recurring setup authority is
    also choosing the mode the AI is allowed to use. The operator's ALLOW_LIVE_ACTIONS
    kill switch is separate and cannot be bypassed here.
    """
    if org_id is None:
        raise ConsentError("Choose a workspace before granting setup consent.")
    payload = json.dumps(list(scopes or []), sort_keys=True)
    extra = json.dumps(metadata or {}, sort_keys=True)
    with db.get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO analytics_consents (org_id, user_id, kind, version, scopes, "
            "accepted_at, revoked_at, metadata, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, '', ?, ?)",
            (org_id, user_id, kind, VERSION, payload, db.now_str(), extra, db.now_str()),
        )
        consent_id = cur.lastrowid
    settings.set_value("ANALYTICS_AUTONOMY", "1", actor=actor or "consent", org_id=org_id)
    return get(consent_id)


def get(consent_id):
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM analytics_consents WHERE id = ?", (consent_id,)
        ).fetchone()
    return _out(row)


def revoke(org_id, actor="", kind=KIND):
    """Revoke every active row and switch autonomous writes off.

    Updating all rows rather than only the latest protects against a raced double grant
    leaving an older, unseen active row behind.
    """
    now = db.now_str()
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE analytics_consents SET revoked_at = ? "
            "WHERE org_id = ? AND kind = ? AND (revoked_at = '' OR revoked_at IS NULL)",
            (now, org_id, kind),
        )
    settings.set_value("ANALYTICS_AUTONOMY", "0", actor=actor or "revoke", org_id=org_id)
    return status(org_id, kind)
