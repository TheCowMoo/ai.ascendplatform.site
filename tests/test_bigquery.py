"""BigQuery phase: link creation, aggregate queries, upserts and routes.

Run: .\\venv\\Scripts\\python.exe tests\\test_bigquery.py

No Google network call is made. The Admin and BigQuery transports are replaced with
fakes that record the method and payload.
"""

import json
import os
import sys
import tempfile
import urllib.parse
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="bigquery-"), "test.db").replace("\\", "/")
os.environ.setdefault("NOTIFICATIONS_ENABLED", "0")
os.environ.setdefault("OAUTH_TOKEN_KEY", "test-bigquery-key")
os.environ["GA4_OAUTH_CLIENT_ID"] = "test-google-client"
os.environ["GA4_OAUTH_CLIENT_SECRET"] = "test-google-secret"
os.environ["GA4_OAUTH_REDIRECT_URI"] = (
    "https://platform.test/api/analytics/oauth/google/callback")

import analytics  # noqa: E402
import analytics_oauth  # noqa: E402
import app as appmod  # noqa: E402
import bigquery  # noqa: E402
import db  # noqa: E402
import ga4_admin  # noqa: E402
import orgs as orgs_service  # noqa: E402
import settings  # noqa: E402

TAG = uuid.uuid4().hex[:8]
ORG_NAME = "BigQuery Client %s" % TAG
ORG_EMAIL = "bigquery-%s@example.test" % TAG
ORG_PASSWORD = "bigquery-pass-1234"
STATE = {}
FAILED = []


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name +
          ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


def login(email, password):
    client = appmod.app.test_client()
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.get_data(as_text=True)
    return client


def setup():
    db.init_db()
    STATE["org"] = orgs_service.create_org({
        "name": ORG_NAME, "email": ORG_EMAIL, "password": ORG_PASSWORD,
    })
    analytics_oauth.save_connection(
        STATE["org"]["id"],
        {
            "access_token": "bq-access",
            "refresh_token": "bq-refresh",
            "expires_in": 3600,
            "scope": " ".join(analytics_oauth.READ_SCOPES
                              + analytics_oauth.WRITE_SCOPES
                              + analytics_oauth.BIGQUERY_SCOPES),
        },
        account_email="bq@example.test",
        connection_scope="client",
    )
    connection = analytics_oauth.get_connection(STATE["org"]["id"], "client")
    STATE["source"] = analytics.save_source({
        "kind": "ga4",
        "external_id": "properties/123456789",
        "label": "BigQuery property",
        "credential_ref": analytics_oauth.source_marker(connection["id"]),
    }, org_id=STATE["org"]["id"])
    STATE["property"] = analytics.save_property({
        "org_id": STATE["org"]["id"],
        "source_id": STATE["source"]["id"],
        "property_id": "123456789",
        "display_name": "BigQuery property",
        "account_name": "Test account",
        "time_zone": "Etc/UTC",
        "currency_code": "USD",
    })
    STATE["client"] = login(ORG_EMAIL, ORG_PASSWORD)


def test_scope_and_link_payload():
    scopes = analytics_oauth.scopes_for(write=True, bigquery=True)
    check("BigQuery scope is requested",
          analytics_oauth.SCOPE_BIGQUERY_READONLY in scopes, True)
    auth_url = analytics_oauth.authorization_url("state", bigquery=True)
    query = urllib.parse.parse_qs(urllib.parse.urlparse(auth_url).query)
    check("the consent URL carries BigQuery",
          analytics_oauth.SCOPE_BIGQUERY_READONLY in query.get("scope", [""])[0], True)

    calls = []

    def fake(url, headers=None, timeout=20, method="GET", payload=None):
        calls.append({"url": url, "method": method, "payload": payload})
        if method == "POST" and "/bigQueryLinks" in url:
            return 200, json.dumps({
                "name": "properties/123456789/bigQueryLinks/1",
                "project": "projects/123",
                "datasetLocation": "EU",
                "dailyExportEnabled": True,
                "streamingExportEnabled": False,
            })
        if method == "GET" and "/bigQueryLinks" in url:
            return 200, json.dumps({"bigqueryLinks": [{
                "name": "properties/123456789/bigQueryLinks/1",
                "project": "projects/123",
                "datasetLocation": "EU",
                "dailyExportEnabled": True,
            }]})
        return 404, "{}"

    original = ga4_admin._transport
    ga4_admin._transport = fake
    try:
        link = ga4_admin.create_bigquery_link(
            "token", "properties/123456789", "my-project", "EU", True, False)
        links = ga4_admin.list_bigquery_links("token", "properties/123456789")
    finally:
        ga4_admin._transport = original

    check("the link resource is returned",
          link.get("name"), "properties/123456789/bigQueryLinks/1")
    check("the link project is returned", link.get("project"), "projects/123")
    check("the link list is returned", len(links), 1)
    create = [c for c in calls if c["method"] == "POST"][0]
    body = create.get("payload") or {}
    check("link creation uses v1alpha", "/v1alpha/" in create["url"], True)
    check("link project is a resource name",
          body.get("project"), "projects/my-project")
    check("link location", body.get("datasetLocation"), "EU")
    check("daily export is enabled", body.get("dailyExportEnabled"), True)
    check("streaming export is off", body.get("streamingExportEnabled"), False)
def test_run_query():
    calls = []

    def fake(url, method="GET", payload=None, headers=None, timeout=30):
        calls.append({"url": url, "method": method, "payload": payload})
        if method == "POST":
            return 200, json.dumps({
                "jobComplete": True,
                "schema": {"fields": [
                    {"name": "event_date"}, {"name": "event_name"},
                    {"name": "event_count"}]},
                "rows": [{"f": [{"v": "20261124"}, {"v": "page_view"},
                                {"v": "42"}]}],
            })
        return 200, "{}"

    original = bigquery._transport
    bigquery._transport = fake
    try:
        rows = bigquery.run_query("token", "my-project", "SELECT 1", location="EU")
    finally:
        bigquery._transport = original
    check("the query row is decoded", rows,
          [{"event_date": "20261124", "event_name": "page_view",
            "event_count": "42"}])
    body = calls[0].get("payload") or {}
    check("the query is standard SQL", body.get("useLegacySql"), False)
    check("the query has a byte ceiling",
          bool(body.get("maximumBytesBilled")), True)
    check("the query has a job timeout", body.get("jobTimeoutMs"), "60000")
    check("the query location is sent", body.get("location"), "EU")
    check("the query endpoint is jobs.query",
          calls[0]["url"].endswith("/projects/my-project/queries"), True)

    # A job that has not finished is polled, not treated as empty.
    polls = []

    def slow(url, method="GET", payload=None, headers=None, timeout=30):
        polls.append({"url": url, "method": method})
        if method == "POST":
            return 200, json.dumps({
                "jobComplete": False,
                "jobReference": {"jobId": "job-1", "location": "EU"},
            })
        return 200, json.dumps({
            "jobComplete": True,
            "schema": {"fields": [{"name": "event_name"}]},
            "rows": [{"f": [{"v": "form_submit"}]}],
        })

    original = bigquery._transport
    bigquery._transport = slow
    try:
        rows = bigquery.run_query(
            "token", "my-project", "SELECT 1", location="EU", poll_interval=0)
    finally:
        bigquery._transport = original
    check("a slow job is polled", rows, [{"event_name": "form_submit"}])
    check("the poll calls the job result endpoint",
          "/queries/job-1" in polls[1]["url"], True)
    try:
        bigquery.run_query("token", "bad project", "SELECT 1")
        raised = False
    except bigquery.BigQueryError:
        raised = True
    check("an invalid project is refused", raised, True)
def test_sync_and_upsert():
    property_row = dict(STATE["property"])
    property_row.update({
        "bigquery_project": "my-project",
        "bigquery_dataset": "analytics_123456789",
        "bigquery_location": "EU",
        "bigquery_link_status": "linked",
    })
    property_row = analytics.save_property(property_row)
    value = {"events": 42}

    def fake(url, method="GET", payload=None, headers=None, timeout=30):
        query = (payload or {}).get("query") or ""
        if "COUNT(*) AS event_count" in query:
            return 200, json.dumps({
                "jobComplete": True,
                "schema": {"fields": [
                    {"name": "event_date"}, {"name": "event_name"},
                    {"name": "event_count"}, {"name": "users"}]},
                "rows": [{"f": [
                    {"v": "20261124"}, {"v": "page_view"},
                    {"v": str(value["events"])}, {"v": "7"}]}],
            })
        if "event_name = 'page_view'" in query:
            return 200, json.dumps({
                "jobComplete": True,
                "schema": {"fields": [
                    {"name": "event_date"}, {"name": "page_path"},
                    {"name": "views"}, {"name": "users"}]},
                "rows": [{"f": [
                    {"v": "20261124"}, {"v": "/pricing"},
                    {"v": "5"}, {"v": "3"}]}],
            })
        if "event_name = 'session_start'" in query:
            return 200, json.dumps({
                "jobComplete": True,
                "schema": {"fields": [
                    {"name": "event_date"}, {"name": "source"},
                    {"name": "medium"}, {"name": "sessions"}, {"name": "users"}]},
                "rows": [{"f": [
                    {"v": "20261124"}, {"v": "google"}, {"v": "cpc"},
                    {"v": "4"}, {"v": "3"}]}],
            })
        return 200, json.dumps({"jobComplete": True, "schema": {"fields": []},
                                "rows": []})

    original = bigquery._transport
    bigquery._transport = fake
    try:
        result = bigquery.sync(STATE["source"], property_row, days=7)
    finally:
        bigquery._transport = original
    check("BigQuery sync succeeded", result.get("ok"), True)
    check("all three reports ran", result.get("reports"),
          list(bigquery.QUERIES))
    check("six facts were written", result.get("rows"), 6)
    rows = analytics.facts(STATE["org"]["id"], "2000-01-01")
    event_fact = [row for row in rows
                  if row["report"] == "bq_events"
                  and row["metric"] == "eventCount"][0]
    check("the event count is stored", event_fact["value"], 42.0)
    page_fact = [row for row in rows
                 if row["report"] == "bq_pages" and row["metric"] == "views"][0]
    check("the page view count is stored", page_fact["value"], 5.0)
    check("the page path is a path, not a URL",
          page_fact["dimensions"].get("page_path"), "/pricing")
    acquisition = [row for row in rows
                   if row["report"] == "bq_acquisition"
                   and row["metric"] == "sessions"][0]
    check("the acquisition source is stored",
          acquisition["dimensions"].get("source"), "google")
    check("the link status is synced",
          analytics.property_for_source(STATE["source"]["id"])
          .get("bigquery_link_status", "").startswith("synced"), True)

    # A later export can revise a day. The second sync must replace, not duplicate.
    value["events"] = 50
    original = bigquery._transport
    bigquery._transport = fake
    try:
        bigquery.sync(STATE["source"], property_row, days=7)
    finally:
        bigquery._transport = original
    rows = analytics.facts(STATE["org"]["id"], "2000-01-01")
    event_values = [row["value"] for row in rows
                    if row["report"] == "bq_events"
                    and row["metric"] == "eventCount"]
    check("a re-sync upserts the value", event_values, [50.0])
    check("a re-sync does not duplicate the row", len(event_values), 1)
def test_routes():
    org = STATE["org"]["id"]
    original_link = ga4_admin.create_bigquery_link
    original_sync = bigquery.sync

    def fake_link(token, property_id, project, location, daily_export=True,
                  streaming_export=False):
        return {"name": "properties/123456789/bigQueryLinks/1",
                "project": "projects/123", "dataset_location": location,
                "daily_export_enabled": daily_export,
                "streaming_export_enabled": streaming_export}

    def fake_sync(source, property_row=None, days=7):
        return {"ok": True, "rows": 6, "reports": list(bigquery.QUERIES),
                "errors": [], "window": {"start": "20261118", "end": "20261124"},
                "project": "route-project", "dataset": "analytics_123456789"}

    ga4_admin.create_bigquery_link = fake_link
    bigquery.sync = fake_sync
    settings.set_value("ALLOW_LIVE_ACTIONS", "1", actor="test")
    try:
        response = STATE["client"].post("/api/analytics/google/bigquery-link", json={
            "connection_scope": "client",
            "source_id": STATE["source"]["id"],
            "project": "route-project",
            "location": "US",
            "daily_export": True,
            "streaming_export": False,
        })
        check("the link route returns 201", response.status_code, 201)
        body = response.get_json() or {}
        check("the link response has the dataset",
              (body.get("property") or {}).get("bigquery_dataset"),
              "analytics_123456789")
        meta = analytics.property_for_source(STATE["source"]["id"])
        check("the property stores the project",
              meta.get("bigquery_project"), "route-project")
        check("the property stores the location",
              meta.get("bigquery_location"), "US")
        check("the property is linked",
              meta.get("bigquery_link_status"), "linked")

        analytics.record_action(org, "bigquery_link", actor="test")
        response = STATE["client"].post("/api/analytics/google/bigquery-link", json={
            "connection_scope": "client",
            "source_id": STATE["source"]["id"],
            "project": "route-project",
            "location": "US",
        })
        check("the BigQuery link route has a rate limit", response.status_code, 429)

        response = STATE["client"].post("/api/analytics/bigquery/sync", json={
            "connection_scope": "client",
            "source_id": STATE["source"]["id"],
            "days": 7,
        })
        check("the sync route returns 200", response.status_code, 200)
        check("the sync route reports rows",
              (response.get_json() or {}).get("rows"), 6)
        actions = [row.get("action") for row in analytics.actions(org)]
        check("the link is audited", "bigquery_link" in actions, True)
        check("the sync is audited", "bigquery_sync" in actions, True)
    finally:
        ga4_admin.create_bigquery_link = original_link
        bigquery.sync = original_sync
        settings.clear_value("ALLOW_LIVE_ACTIONS", actor="test")
    print("the BigQuery link and sync routes are wired to the audit trail")


def main():
    setup()
    test_scope_and_link_payload()
    test_run_query()
    test_sync_and_upsert()
    test_routes()
    if FAILED:
        print("\n%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
        return 1
    print("\nBIGQUERY OK - link, aggregate queries, upsert and routes are wired")
    return 0


if __name__ == "__main__":
    sys.exit(main())


