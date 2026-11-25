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

import datetime
import json

KINDS = ("ghl", "caddy", "ga4", "meta")
# A client workspace may configure only the adapters that are actually ready for it.
# Caddy logs are platform infrastructure (and are read automatically); GHL/Meta adapters
# are not written yet. The operator can still configure the full catalogue.
CLIENT_KINDS = ("ga4",)

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
    credential = str(item.get("credential_ref") or "")
    if item.get("kind") == "ga4":
        item["auth_mode"] = "oauth" if credential.startswith("oauth:") else "service_account"
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
    credential_raw = data.get("credential_ref")
    credential = str(credential_raw or "").strip()
    enabled_raw = data.get("enabled")
    enabled = 1 if (enabled_raw if enabled_raw is not None else True) else 0
    if source_id is not None:
        existing = get_source(source_id)
        if not existing:
            raise SourceError("That data source no longer exists.")
        # A tenant may only edit its own binding. Without this check, a client could
        # change another workspace's source by guessing its id.
        if org_id is not None and existing.get("org_id") != org_id:
            raise SourceError("That data source was not found for this workspace.")
        # A client form does not send credential_ref. Omitting it must preserve the
        # OAuth marker (or a service-account path) instead of silently disconnecting
        # the source.
        if credential_raw is None:
            credential = existing.get("credential_ref") or ""
        if enabled_raw is None:
            enabled = 1 if existing.get("enabled") else 0
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
        conn.execute("DELETE FROM analytics_properties WHERE source_id = ?", (source_id,))
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


def _dimension_key(dimensions):
    return "|".join("%s=%s" % (key, (dimensions or {}).get(key, ""))
                    for key in sorted(dimensions or {}))


def record_facts(org_id, source_id, facts, upsert=False):
    """Idempotent upsert of ``[(report, metric, dimensions, day, value), ...]``.

    ``upsert`` is for the BigQuery export: a daily table can be revised after the first
    read, so the newer aggregate replaces the older one instead of being ignored.
    """
    written = 0
    with get_connection() as conn:
        for report, metric, dimensions, day, value in facts:
            dimensions = dimensions or {}
            key = _dimension_key(dimensions)
            payload = json.dumps(dimensions, sort_keys=True)
            if upsert:
                conn.execute(
                    "INSERT INTO analytics_facts "
                    "(org_id, source_id, report, metric, dimensions, dimension_key, "
                    "day, value) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(org_id, source_id, report, metric, dimension_key, day) "
                    "DO UPDATE SET value = excluded.value, "
                    "dimensions = excluded.dimensions",
                    (org_id, source_id, report, metric, payload, key, day,
                     float(value or 0)),
                )
            else:
                conn.execute(
                    "INSERT OR IGNORE INTO analytics_facts "
                    "(org_id, source_id, report, metric, dimensions, dimension_key, "
                    "day, value) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (org_id, source_id, report, metric, payload, key, day,
                     float(value or 0)),
                )
            written += 1
    return written


def _fact(row):
    item = {key: row[key] for key in row.keys()}
    try:
        item["dimensions"] = json.loads(item.get("dimensions") or "{}")
    except (TypeError, ValueError):
        item["dimensions"] = {}
    return item


def facts(org_id, since, report=None, source_id=None, limit=5000):
    query = ("SELECT source_id, report, metric, dimensions, day, value "
             "FROM analytics_facts WHERE org_id = ? AND day >= ?")
    params = [org_id, since]
    if report:
        query += " AND report = ?"
        params.append(report)
    if source_id:
        query += " AND source_id = ?"
        params.append(source_id)
    query += " ORDER BY day ASC, report ASC, metric ASC LIMIT ?"
    params.append(max(1, int(limit)))
    with get_connection() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_fact(row) for row in rows]


def properties(org_id):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM analytics_properties WHERE org_id = ? ORDER BY id ASC",
            (org_id,),
        ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def property_for_source(source_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM analytics_properties WHERE source_id = ?", (source_id,)
        ).fetchone()
    return {key: row[key] for key in row.keys()} if row else None


def save_property(data):
    """Upsert the metadata for one GA4 source. Returns the stored row."""
    data = data or {}
    org_id = data.get("org_id")
    source_id = data.get("source_id")
    if org_id is None or source_id is None:
        raise SourceError("A property needs a client and a data source.")
    existing = property_for_source(source_id)
    values = (
        int(org_id), int(source_id),
        str(data.get("property_id") or "").strip(),
        str(data.get("display_name") or "").strip(),
        str(data.get("account_name") or "").strip(),
        str(data.get("time_zone") or "").strip(),
        str(data.get("currency_code") or "").strip(),
        data.get("tag_container_id"),
        str(data.get("bigquery_project") or "").strip(),
        str(data.get("bigquery_dataset") or "").strip(),
        str(data.get("bigquery_location") or "").strip(),
        str(data.get("bigquery_link_status") or "").strip(),
        now_str(),
    )
    with get_connection() as conn:
        if existing:
            conn.execute(
                "UPDATE analytics_properties SET property_id = ?, display_name = ?, "
                "account_name = ?, time_zone = ?, currency_code = ?, tag_container_id = ?, "
                "bigquery_project = ?, bigquery_dataset = ?, bigquery_location = ?, "
                "bigquery_link_status = ?, updated_at = ? WHERE id = ?",
                values[2:] + (existing["id"],),
            )
        else:
            conn.execute(
                "INSERT INTO analytics_properties (org_id, source_id, property_id, "
                "display_name, account_name, time_zone, currency_code, tag_container_id, "
                "bigquery_project, bigquery_dataset, bigquery_location, "
                "bigquery_link_status, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values,
            )
    return property_for_source(source_id)


def update_bigquery_status(source_id, status):
    """Record the latest export/sync state on the property row."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE analytics_properties SET bigquery_link_status = ?, updated_at = ? "
            "WHERE source_id = ?", (str(status or "")[:500], now_str(), source_id))
    return property_for_source(source_id)


def containers(org_id):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM tag_containers WHERE org_id = ? ORDER BY id ASC", (org_id,)
        ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def save_container(data):
    """Upsert one GTM container for a client. Returns the stored row."""
    data = data or {}
    org_id = data.get("org_id")
    container_id = str(data.get("container_id") or "").strip()
    if org_id is None or not container_id:
        raise SourceError("A Tag Manager container needs a client and a container id.")
    values = (
        data.get("connection_id"),
        str(data.get("account_id") or "").strip(),
        container_id,
        str(data.get("public_id") or "").strip(),
        str(data.get("name") or "").strip(),
        str(data.get("domain_name") or "").strip(),
        str(data.get("kind") or "web").strip() or "web",
        str(data.get("last_published_version") or "").strip(),
        now_str(),
    )
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM tag_containers WHERE org_id = ? AND container_id = ?",
            (org_id, container_id),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE tag_containers SET connection_id = ?, account_id = ?, public_id = ?, "
                "name = ?, domain_name = ?, kind = ?, last_published_version = ?, "
                "updated_at = ? WHERE id = ?",
                (values[0], values[1], values[3], values[4], values[5], values[6],
                 values[7], values[8], row["id"]),
            )
            stored_id = row["id"]
        else:
            cur = conn.execute(
                "INSERT INTO tag_containers (org_id, connection_id, account_id, "
                "container_id, public_id, name, domain_name, kind, last_published_version, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (org_id,) + values,
            )
            stored_id = cur.lastrowid
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM tag_containers WHERE id = ?",
                           (stored_id,)).fetchone()
    return {key: row[key] for key in row.keys()} if row else None


def record_action(org_id, action, target="", payload=None, status="ok", result="", actor=""):
    """Record a GA4/GTM/site write. Never raises into the caller's happy path."""
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO analytics_actions (org_id, action, target, payload, status, "
                "result, actor) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (org_id, str(action or "")[:80], str(target or "")[:300],
                 json.dumps(payload or {}), str(status or "ok")[:40],
                 str(result or "")[:1000], str(actor or "")[:120]),
            )
    except Exception:
        return False
    return True


def actions(org_id, limit=50):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM analytics_actions WHERE org_id = ? ORDER BY id DESC LIMIT ?",
            (org_id, max(1, int(limit))),
        ).fetchall()
    out = []
    for row in rows:
        item = {key: row[key] for key in row.keys()}
        try:
            item["payload"] = json.loads(item.get("payload") or "{}")
        except (TypeError, ValueError):
            item["payload"] = {}
        out.append(item)
    return out


def recent_action_count(org_id, action=None, minutes=60):
    """How many setup writes this client has recorded in the recent window."""
    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(minutes=max(1, int(minutes or 60)))
              ).strftime("%Y-%m-%d %H:%M:%S")
    query = "SELECT COUNT(*) FROM analytics_actions WHERE org_id = ? AND created_at >= ?"
    params = [org_id, cutoff]
    if action:
        query += " AND action = ?"
        params.append(action)
    with get_connection() as conn:
        return int(conn.execute(query, tuple(params)).fetchone()[0])


def prune_actions(days=180, org_id=None):
    """Delete old write-audit rows. Open operational history is kept for ``days``."""
    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(days=max(1, int(days or 180)))
              ).strftime("%Y-%m-%d %H:%M:%S")
    query = "DELETE FROM analytics_actions WHERE created_at < ?"
    params = [cutoff]
    if org_id is not None:
        query += " AND org_id = ?"
        params.append(org_id)
    with get_connection() as conn:
        cur = conn.execute(query, tuple(params))
        return cur.rowcount or 0


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

