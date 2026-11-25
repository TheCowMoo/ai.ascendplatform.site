"""GA4 OAuth: start, callback, refresh and source wiring.

Run: .\\venv\\Scripts\\python.exe tests\\test_ga4_oauth.py

No Google network call is made. The OAuth transport is replaced with a fake token
response, so the test exercises our state handling, source creation, refresh path and
client scoping without needing real credentials.
"""

import os
import sys
import tempfile
import urllib.parse
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="ga4-oauth-"), "test.db").replace("\\", "/")
os.environ.setdefault("NOTIFICATIONS_ENABLED", "0")
os.environ["GA4_OAUTH_CLIENT_ID"] = "test-google-client"
os.environ["GA4_OAUTH_CLIENT_SECRET"] = "test-google-secret"
os.environ["GA4_OAUTH_REDIRECT_URI"] = "https://platform.test/api/analytics/oauth/google/callback"

import analytics  # noqa: E402
import analytics_oauth  # noqa: E402
import app as appmod  # noqa: E402
import db  # noqa: E402
import ga4  # noqa: E402
import orgs as orgs_service  # noqa: E402

TAG = uuid.uuid4().hex[:8]
ORG_NAME = "GA4 OAuth Client %s" % TAG
ORG_EMAIL = "ga4-oauth-%s@example.test" % TAG
ORG_PASSWORD = "ga4-oauth-pass-1234"
OTHER_NAME = "GA4 OAuth Other %s" % TAG
OTHER_EMAIL = "ga4-oauth-other-%s@example.test" % TAG
MEMBER_EMAIL = "ga4-oauth-member-%s@example.test" % TAG
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


def owner():
    return login("admin@example.com", "admin123")


def cleanup():
    with db.get_connection() as conn:
        conn.execute("DELETE FROM metrics_daily WHERE org_id IN "
                     "(SELECT id FROM orgs WHERE name IN (?, ?))", (ORG_NAME, OTHER_NAME))
        conn.execute("DELETE FROM oauth_connections WHERE org_id IN "
                     "(SELECT id FROM orgs WHERE name IN (?, ?))", (ORG_NAME, OTHER_NAME))
        conn.execute("DELETE FROM data_sources WHERE org_id IN "
                     "(SELECT id FROM orgs WHERE name IN (?, ?))", (ORG_NAME, OTHER_NAME))
        conn.execute("DELETE FROM users WHERE org_id IN "
                     "(SELECT id FROM orgs WHERE name IN (?, ?))", (ORG_NAME, OTHER_NAME))
        conn.execute("DELETE FROM orgs WHERE name IN (?, ?)", (ORG_NAME, OTHER_NAME))


def setup():
    cleanup()
    db.init_db()
    STATE["org"] = orgs_service.create_org({
        "name": ORG_NAME, "email": ORG_EMAIL, "password": ORG_PASSWORD,
    })
    STATE["other"] = orgs_service.create_org({
        "name": OTHER_NAME, "email": OTHER_EMAIL, "password": "ga4-oauth-other-pass",
    })
    orgs_service.add_org_user(STATE["org"]["id"], {
        "email": MEMBER_EMAIL, "name": "GA4 Member",
        "password": "ga4-oauth-member-pass", "role": "member",
    })
    STATE["client"] = login(ORG_EMAIL, ORG_PASSWORD)
    STATE["member"] = login(MEMBER_EMAIL, "ga4-oauth-member-pass")
    STATE["owner"] = owner()


def start(client, **payload):
    response = client.post("/api/analytics/oauth/google/start", json=payload)
    assert response.status_code == 200, response.get_data(as_text=True)
    auth_url = response.get_json()["auth_url"]
    query = urllib.parse.parse_qs(urllib.parse.urlparse(auth_url).query)
    return auth_url, query


def test_start_url_and_state():
    auth_url, query = start(STATE["client"], external_id="123456789", label="Main site")
    check("start points at Google", auth_url.startswith(analytics_oauth.AUTH_URL), True)
    check("read-only scope", query.get("scope"), [analytics_oauth.SCOPE])
    check("offline access", query.get("access_type"), ["offline"])
    check("consent is requested", "consent" in (query.get("prompt") or [""])[0], True)
    check("state is present", bool(query.get("state")), True)
    check("redirect URI matches", query.get("redirect_uri"),
          [os.environ["GA4_OAUTH_REDIRECT_URI"]])
    STATE["auth_url"] = auth_url
    STATE["state"] = query["state"][0]
    print("start URL carries scope, redirect and one-time state")


def test_missing_configuration_is_explicit():
    old = {key: os.environ.pop(key, None) for key in (
        "GA4_OAUTH_CLIENT_ID", "GA4_OAUTH_CLIENT_SECRET", "GA4_OAUTH_REDIRECT_URI")}
    try:
        response = STATE["client"].post("/api/analytics/oauth/google/start",
                                        json={"external_id": "123456789"})
        check("missing OAuth config is a 400", response.status_code, 400)
        check("the error names the missing setting",
              "GA4_OAUTH_CLIENT_ID" in response.get_data(as_text=True), True)
    finally:
        for key, value in old.items():
            if value is not None:
                os.environ[key] = value
    print("missing OAuth configuration is explained instead of redirecting")


def test_callback_saves_connection_and_source():
    original = analytics_oauth.exchange_code
    analytics_oauth.exchange_code = lambda code: {
        "access_token": "access-one",
        "refresh_token": "refresh-one",
        "expires_in": 3600,
        "scope": analytics_oauth.SCOPE,
    }
    try:
        response = STATE["client"].get(
            "/api/analytics/oauth/google/callback?code=fake-code&state="
            + urllib.parse.quote(STATE["state"]))
    finally:
        analytics_oauth.exchange_code = original
    check("callback redirects to the console", response.status_code, 302)
    check("callback lands on the success route",
          response.headers.get("Location", "").endswith("/#/app/analytics/oauth-ok"), True)

    connection = analytics_oauth.get_connection(STATE["org"]["id"])
    check("the refresh token was stored", connection.get("refresh_token"), "refresh-one")
    source = [row for row in analytics.sources(STATE["org"]["id"])
              if row.get("kind") == "ga4"][0]
    check("the source has the OAuth marker",
          source["credential_ref"], analytics_oauth.source_marker(connection["id"]))
    check("the source reports OAuth mode", source["auth_mode"], "oauth")
    check("the property was normalized", source["external_id"], "properties/123456789")
    check("the connection label was kept", source["label"], "Main site")

    notice = STATE["client"].get("/api/analytics/oauth/notice").get_json()
    check("the success notice is available", notice.get("notice", {}).get("kind"), "ok")
    check("the notice is one-shot",
          STATE["client"].get("/api/analytics/oauth/notice").get_json().get("notice"), None)
    print("callback stored the grant, created the source and returned a notice")


def test_callback_rejects_a_bad_state():
    _auth_url, query = start(STATE["client"], external_id="987654321", label="Bad state")
    before = len(analytics.sources(STATE["org"]["id"]))
    response = STATE["client"].get(
        "/api/analytics/oauth/google/callback?code=fake&state=not-the-state")
    check("bad state redirects to the error route",
          response.headers.get("Location", "").endswith("/#/app/analytics/oauth-error"), True)
    check("bad state creates no source", len(analytics.sources(STATE["org"]["id"])), before)
    print("a mismatched OAuth state is rejected without touching sources")


def test_members_cannot_start_oauth():
    response = STATE["member"].post("/api/analytics/oauth/google/start",
                                    json={"external_id": "123456789"})
    check("members are refused OAuth start", response.status_code, 403)
    print("members cannot start a Google Analytics connection")


def test_access_token_refresh_preserves_the_refresh_token():
    org_id = STATE["org"]["id"]
    with db.get_connection() as conn:
        conn.execute("UPDATE oauth_connections SET expires_at = '2000-01-01 00:00:00' "
                     "WHERE provider = ? AND org_id = ?", (analytics_oauth.PROVIDER, org_id))
    original = analytics_oauth.refresh_access_token
    analytics_oauth.refresh_access_token = lambda refresh: {
        "access_token": "access-two", "expires_in": 3600,
        "scope": analytics_oauth.SCOPE,
    }
    try:
        token = analytics_oauth.access_token_for_org(org_id)
    finally:
        analytics_oauth.refresh_access_token = original
    check("refresh returns the new access token", token, "access-two")
    connection = analytics_oauth.get_connection(org_id)
    check("refresh keeps the original refresh token",
          connection.get("refresh_token"), "refresh-one")
    print("an expired access token refreshes without losing offline access")


def test_ga4_sync_uses_the_oauth_token():
    org_id = STATE["org"]["id"]
    source = [row for row in analytics.sources(org_id) if row.get("kind") == "ga4"][0]
    original_token = analytics_oauth.access_token_for_org
    original_report = ga4.run_report
    seen = {}
    analytics_oauth.access_token_for_org = lambda org: "oauth-access-token"
    ga4.run_report = lambda property_id, token, days=7: seen.update(
        {"property_id": property_id, "token": token}) or {
        "rows": [{"dimensionValues": [{"value": "20261019"}, {"value": "/"}],
                  "metricValues": [{"value": "4"}, {"value": "9"}]}]
    }
    try:
        result = ga4.sync(source)
    finally:
        analytics_oauth.access_token_for_org = original_token
        ga4.run_report = original_report
    check("sync passes the property id", seen.get("property_id"), "properties/123456789")
    check("sync uses the OAuth bearer token", seen.get("token"), "oauth-access-token")
    check("sync writes metric rows", result.get("rows", 0) > 0, True)
    print("GA4 sync selects the OAuth token path for an OAuth source")


def main():
    try:
        setup()
        test_start_url_and_state()
        test_missing_configuration_is_explicit()
        test_callback_saves_connection_and_source()
        test_callback_rejects_a_bad_state()
        test_members_cannot_start_oauth()
        test_access_token_refresh_preserves_the_refresh_token()
        test_ga4_sync_uses_the_oauth_token()
    finally:
        cleanup()
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nGA4 OAUTH OK - state, callback, refresh and sync are wired")


if __name__ == "__main__":
    main()
