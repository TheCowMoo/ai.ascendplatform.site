"""BigQuery: read the GA4 event export as aggregate facts.

GA4 writes daily ``events_YYYYMMDD`` tables into the linked BigQuery project. This module
runs a small, bounded set of aggregate queries; it never stores raw event rows or
``user_pseudo_id``. The OAuth grant must include ``bigquery.readonly``, and the Google
user needs BigQuery job/create and dataset read access.
"""

import datetime
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import settings

API = "https://bigquery.googleapis.com/bigquery/v2"
SCOPE = "https://www.googleapis.com/auth/bigquery.readonly"
# A ceiling on a single query's scanned bytes. BigQuery rejects a query over the cap
# instead of billing an accidental full-history scan.
MAX_BYTES_BILLED = "2000000000"
PROJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{3,61}$")
DATASET_RE = re.compile(r"^[A-Za-z0-9_]{1,1024}$")
LOCATION_RE = re.compile(r"^[A-Za-z0-9_-]{2,30}$")

QUERIES = ("bq_events", "bq_pages", "bq_acquisition")


class BigQueryError(Exception):
    """A setup or query problem phrased for the person who can fix it."""


def _identifiers(project, dataset):
    project = str(project or "").strip()
    dataset = str(dataset or "").strip()
    if not PROJECT_RE.fullmatch(project):
        raise BigQueryError("Enter a valid Google Cloud project ID for the export.")
    if not DATASET_RE.fullmatch(dataset):
        raise BigQueryError("Enter a valid BigQuery dataset name, for example "
                            "analytics_123456789.")
    return project, dataset


def export_window(days=7):
    """``(start, end)`` YYYYMMDD for completed daily export tables."""
    days = max(1, min(90, int(days or 7)))
    end = (datetime.datetime.now(datetime.timezone.utc).date()
           - datetime.timedelta(days=1))
    start = end - datetime.timedelta(days=days - 1)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def _table(project, dataset):
    return "`%s.%s.events_*`" % (project, dataset)


def event_sql(project, dataset, start, end):
    return """
SELECT event_date, event_name,
       COUNT(*) AS event_count,
       COUNT(DISTINCT user_pseudo_id) AS users
FROM %s
WHERE _TABLE_SUFFIX BETWEEN '%s' AND '%s'
GROUP BY event_date, event_name
ORDER BY event_date, event_count DESC
LIMIT 10000
""" % (_table(project, dataset), start, end)


def page_sql(project, dataset, start, end):
    # The export's page_location includes the full URL. Keep only the path and drop
    # query/fragment so campaign parameters or personal data are never persisted here.
    return """
SELECT event_date,
       COALESCE(
         REGEXP_REPLACE(
           REGEXP_REPLACE(
             (SELECT value.string_value FROM UNNEST(event_params)
              WHERE key = 'page_location' LIMIT 1),
             r'^https?://[^/]+', ''),
           r'[?#].*$', ''),
         '(not set)') AS page_path,
       COUNT(*) AS views,
       COUNT(DISTINCT user_pseudo_id) AS users
FROM %s
WHERE _TABLE_SUFFIX BETWEEN '%s' AND '%s'
  AND event_name = 'page_view'
GROUP BY event_date, page_path
ORDER BY event_date, views DESC
LIMIT 10000
""" % (_table(project, dataset), start, end)


def acquisition_sql(project, dataset, start, end):
    return """
SELECT event_date,
       COALESCE(traffic_source.source, '(direct)') AS source,
       COALESCE(traffic_source.medium, '(none)') AS medium,
       COUNT(*) AS sessions,
       COUNT(DISTINCT user_pseudo_id) AS users
FROM %s
WHERE _TABLE_SUFFIX BETWEEN '%s' AND '%s'
  AND event_name = 'session_start'
GROUP BY event_date, source, medium
ORDER BY event_date, sessions DESC
LIMIT 10000
""" % (_table(project, dataset), start, end)


def query_for(report, project, dataset, start, end):
    builders = {
        "bq_events": event_sql,
        "bq_pages": page_sql,
        "bq_acquisition": acquisition_sql,
    }
    builder = builders.get(report)
    if not builder:
        raise BigQueryError("Unknown BigQuery report %r." % report)
    return builder(project, dataset, start, end)


def _transport(url, method="GET", payload=None, headers=None, timeout=30):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers or {},
                                     method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def _request(token, url, method="GET", payload=None):
    headers = {
        "Authorization": "Bearer %s" % token,
        "Accept": "application/json",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    status, body = _transport(url, method=method, payload=payload, headers=headers)
    if status in (401, 403):
        raise BigQueryError(
            "BigQuery did not grant access to this project. Check that the Google account "
            "has BigQuery Job User and read access to the GA4 export dataset, then "
            "reconnect with BigQuery access.")
    if status == 404:
        raise BigQueryError(
            "The GA4 BigQuery export dataset was not found. GA4 can take up to 24 hours "
            "to create the first export table after the link is created.")
    if status >= 400:
        raise BigQueryError("BigQuery returned %s: %s" % (status, str(body)[:400]))
    try:
        return json.loads(body)
    except ValueError:
        raise BigQueryError("BigQuery returned an unreadable response.")


def _query_error(data):
    errors = data.get("errors") or []
    if not errors:
        return ""
    first = errors[0] if isinstance(errors[0], dict) else {"message": str(errors[0])}
    return first.get("message") or first.get("reason") or "BigQuery query failed."


def _rows(data):
    fields = (data.get("schema") or {}).get("fields") or []
    names = [field.get("name") or "" for field in fields]
    rows = []
    for row in data.get("rows") or []:
        values = [cell.get("v") for cell in row.get("f") or []]
        rows.append(dict(zip(names, values)))
    return rows


def run_query(token, project, sql, location="", timeout_ms=10000,
              max_results=10000, poll_interval=1.0):
    """Run one BigQuery SQL statement and return decoded rows.

    ``jobs.query`` may return before a large job completes; this polls the job result a
    bounded number of times rather than treating an incomplete response as empty data.
    """
    project = str(project or "").strip()
    if not PROJECT_RE.fullmatch(project):
        raise BigQueryError("Enter a valid Google Cloud project ID for the export.")
    if location and not LOCATION_RE.fullmatch(str(location).strip()):
        raise BigQueryError("Enter a valid BigQuery dataset location, for example US or EU.")
    payload = {
        "query": sql,
        "useLegacySql": False,
        "maxResults": max_results,
        "timeoutMs": timeout_ms,
        "jobTimeoutMs": "60000",
        "maximumBytesBilled": MAX_BYTES_BILLED,
    }
    if location:
        payload["location"] = location
    url = API + "/projects/" + urllib.parse.quote(project) + "/queries"
    data = _request(token, url, method="POST", payload=payload)
    message = _query_error(data)
    if message:
        raise BigQueryError("BigQuery query failed: %s" % message)
    job = data.get("jobReference") or {}
    job_id = job.get("jobId") or ""
    job_location = job.get("location") or location
    waited = 0
    while data.get("jobComplete") is False and job_id and waited < 30:
        time.sleep(poll_interval)
        waited += 1
        query = {"timeoutMs": timeout_ms, "maxResults": max_results}
        if job_location:
            query["location"] = job_location
        result_url = (API + "/projects/" + urllib.parse.quote(project)
                      + "/queries/" + urllib.parse.quote(job_id) + "?"
                      + urllib.parse.urlencode(query))
        data = _request(token, result_url)
        message = _query_error(data)
        if message:
            raise BigQueryError("BigQuery query failed: %s" % message)
    if data.get("jobComplete") is False:
        raise BigQueryError(
            "BigQuery did not finish this query in time. Try a shorter day range or run "
            "it again later.")
    return _rows(data)


def _number(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _day(value):
    raw = str(value or "")
    if len(raw) == 8 and raw.isdigit():
        return "%s-%s-%s" % (raw[:4], raw[4:6], raw[6:8])
    return raw


def facts_for(report, rows):
    """Aggregate BigQuery rows into the same fact shape the GA4 reports use."""
    facts = []
    for row in rows or []:
        day = _day(row.get("event_date"))
        if report == "bq_events":
            dims = {"event_name": str(row.get("event_name") or "(not set)")}
            facts.append((report, "eventCount", dims, day, _number(row.get("event_count"))))
            facts.append((report, "users", dims, day, _number(row.get("users"))))
        elif report == "bq_pages":
            dims = {"page_path": str(row.get("page_path") or "(not set)")}
            facts.append((report, "views", dims, day, _number(row.get("views"))))
            facts.append((report, "users", dims, day, _number(row.get("users"))))
        elif report == "bq_acquisition":
            dims = {
                "source": str(row.get("source") or "(direct)"),
                "medium": str(row.get("medium") or "(none)"),
            }
            facts.append((report, "sessions", dims, day, _number(row.get("sessions"))))
            facts.append((report, "users", dims, day, _number(row.get("users"))))
    return facts


def _token(source):
    import analytics_oauth
    import ga4

    if analytics_oauth.is_oauth_source(source):
        try:
            return analytics_oauth.bigquery_token_for_source(source)
        except analytics_oauth.OAuthError as exc:
            raise BigQueryError(str(exc))
    key_path = (source.get("credential_ref") or "").strip()
    if not key_path:
        key_path = settings.get("GA4_SERVICE_ACCOUNT_KEY_PATH")
    if not key_path:
        raise BigQueryError(
            "BigQuery needs a Google connection with BigQuery access. Connect Google "
            "in Analytics, or configure the GA4 service-account key for the export.")
    try:
        return ga4.access_token(key_path, scope=SCOPE)
    except Exception as exc:
        raise BigQueryError("Could not get a BigQuery token: %s" % exc)


def sync(source, property=None, days=7):
    """Read the curated BigQuery exports and store daily aggregate facts."""
    import analytics
    from db import now_str

    if not source or not source.get("id"):
        raise BigQueryError("Choose a GA4 source first.")
    property = property or analytics.property_for_source(source["id"])
    if not property:
        raise BigQueryError("Connect a GA4 property before syncing BigQuery.")
    project = str(property.get("bigquery_project") or "").strip()
    dataset = str(property.get("bigquery_dataset") or "").strip()
    location = str(property.get("bigquery_location") or "").strip()
    if not project or not dataset:
        raise BigQueryError("Link this GA4 property to BigQuery first.")
    project, dataset = _identifiers(project, dataset)
    token = _token(source)
    start, end = export_window(days)
    facts_written = 0
    used, errors = [], []
    for report in QUERIES:
        try:
            rows = run_query(
                token, project, query_for(report, project, dataset, start, end),
                location=location)
        except Exception as exc:
            errors.append("%s: %s" % (report, str(exc)[:250]))
            continue
        facts_written += analytics.record_facts(
            source["org_id"], source["id"], facts_for(report, rows), upsert=True)
        used.append(report)
    if not used:
        analytics.update_bigquery_status(
            source["id"], "error: %s" % ("; ".join(errors)[:400] or "no data"))
        raise BigQueryError("; ".join(errors) or "BigQuery returned no reports.")
    status = "synced %s" % now_str()
    if errors:
        status = "partial %s" % now_str()
    analytics.update_bigquery_status(source["id"], status)
    return {
        "ok": not errors,
        "rows": facts_written,
        "reports": used,
        "errors": errors,
        "window": {"start": start, "end": end},
        "project": project,
        "dataset": dataset,
    }

