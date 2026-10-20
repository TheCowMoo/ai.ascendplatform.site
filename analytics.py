"""Where a client's numbers come from, and the numbers themselves.

Tenants don't own their analytics accounts uniformly: GHL is the operator's (a
sub-account per client), while GA4 and Meta belong to the client when they have them.
So a **source** is a binding - this org, this external account, this credential
reference - and every adapter reads through it rather than guessing.

Metrics are stored long and narrow (one row per metric, dimension and day) so a site
log, a GA4 report and an ad account all answer the same questions. Aggregates only:
no user-level rows, so no PII can accumulate here even by accident.
"""

from db import get_connection, now_str

KINDS = ("ghl", "caddy", "ga4", "meta")

LABELS = {
    "ghl": "GoHighLevel sub-account",
    "caddy": "Site logs (this server)",
    "ga4": "Google Analytics 4",
    "meta": "Meta ads",
}

# Needed to address the account at all. Site logs need nothing: the files are ours.
REQUIRES_EXTERNAL_ID = ("ghl", "ga4", "meta")


class SourceError(Exception):
    """Something the operator can fix, phrased for them."""


def _out(row):
    item = {key: row[key] for key in row.keys()}
    item["enabled"] = bool(item.get("enabled"))
    item["label"] = item.get("label") or LABELS.get(item["kind"], item["kind"])
    return item


def sources(org_id=None):
    query = "SELECT * FROM data_sources WHERE 1 = 1"
    params = []
    if org_id is not None:
        query += " AND org_id = ?"
        params.append(org_id)
    query += " ORDER BY kind ASC, id ASC"
    with get_connection() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_out(row) for row in rows]


def get_source(source_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM data_sources WHERE id = ?", (source_id,)
        ).fetchone()
    return _out(row) if row else None


def save_source(data, source_id=None, org_id=None):
    """Create or update a binding. Rejects a kind we cannot read."""
    data = data or {}
    kind = (data.get("kind") or "").strip().lower()
    if kind not in KINDS:
        raise SourceError("kind must be one of: %s" % ", ".join(KINDS))
    external_id = (data.get("external_id") or "").strip()
    if kind in REQUIRES_EXTERNAL_ID and not external_id:
        raise SourceError("%s needs an external id (account, property or location)" % kind)
    label = (data.get("label") or "").strip()
    credential = (data.get("credential_ref") or "").strip()
    enabled = 1 if data.get("enabled", True) else 0
    with get_connection() as conn:
        if source_id is None:
            cur = conn.execute(
                "INSERT INTO data_sources (org_id, kind, external_id, label, "
                "credential_ref, enabled) VALUES (?, ?, ?, ?, ?, ?)",
                (org_id, kind, external_id, label, credential, enabled),
            )
            source_id = cur.lastrowid
        else:
            conn.execute(
                "UPDATE data_sources SET kind = ?, external_id = ?, label = ?, "
                "credential_ref = ?, enabled = ?, updated_at = ? WHERE id = ?",
                (kind, external_id, label, credential, enabled, now_str(), source_id),
            )
    return get_source(source_id)


def delete_source(source_id):
    with get_connection() as conn:
        conn.execute("DELETE FROM data_sources WHERE id = ?", (source_id,))
    return True


def record_metrics(org_id, source, points):
    """Idempotent upsert of ``[(metric, dimension, day, value), ...]``.

    ``INSERT OR IGNORE`` plus the unique key means a re-run over an overlapping window
    cannot double-count - which matters, because every pull re-reads the last few days
    to allow for attribution lag.
    """
    written = 0
    with get_connection() as conn:
        for metric, dimension, day, value in points:
            conn.execute(
                "INSERT OR IGNORE INTO metrics_daily "
                "(org_id, source, metric, dimension, day, value) VALUES (?, ?, ?, ?, ?, ?)",
                (org_id, source, metric, dimension or "", day, float(value or 0)),
            )
            written += 1
    return written


def metrics(org_id, since, source=None, metric=None, limit=5000):
    query = ("SELECT source, metric, dimension, day, value FROM metrics_daily "
             "WHERE org_id = ? AND day >= ?")
    params = [org_id, since]
    if source:
        query += " AND source = ?"
        params.append(source)
    if metric:
        query += " AND metric = ?"
        params.append(metric)
    query += " ORDER BY day ASC, metric ASC LIMIT ?"
    params.append(max(1, int(limit)))
    with get_connection() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def start_sync(source_id):
    with get_connection() as conn:
        cur = conn.execute("INSERT INTO sync_runs (source_id) VALUES (?)", (source_id,))
        return cur.lastrowid


def finish_sync(run_id, status, rows=0, error=""):
    """Record how a pull went, and mirror the outcome onto the source itself."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE sync_runs SET finished_at = ?, status = ?, rows = ?, error = ? "
            "WHERE id = ?",
            (now_str(), status, int(rows or 0), (error or "")[:500], run_id),
        )
        if status == "ok":
            conn.execute(
                "UPDATE data_sources SET last_sync_at = ?, last_error = '' "
                "WHERE id = (SELECT source_id FROM sync_runs WHERE id = ?)",
                (now_str(), run_id),
            )
        else:
            conn.execute(
                "UPDATE data_sources SET last_error = ? "
                "WHERE id = (SELECT source_id FROM sync_runs WHERE id = ?)",
                ((error or "")[:500], run_id),
            )
    return True


def latest_sync(source_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM sync_runs WHERE source_id = ? ORDER BY id DESC LIMIT 1",
            (source_id,),
        ).fetchone()
    return {key: row[key] for key in row.keys()} if row else None

