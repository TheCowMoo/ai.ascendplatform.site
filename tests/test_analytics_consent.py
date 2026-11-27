"""Consent, account-ticket and idempotent one-click Analytics setup.

Run: .\\venv\\Scripts\\python.exe tests\\test_analytics_consent.py

No Google network call is made. The admin transports and the setup module's Google
helpers are replaced with fakes that record every resource creation.
"""

import os
import sys
import tempfile
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="analytics-consent-"), "test.db").replace("\\", "/")
os.environ.setdefault("NOTIFICATIONS_ENABLED", "0")
os.environ.setdefault("OAUTH_TOKEN_KEY", "test-analytics-consent-key")
os.environ["GA4_OAUTH_CLIENT_ID"] = "test-google-client"
os.environ["GA4_OAUTH_CLIENT_SECRET"] = "test-google-secret"
os.environ["GA4_OAUTH_REDIRECT_URI"] = (
    "https://platform.test/api/analytics/oauth/google/callback")
os.environ["GA4_ACCOUNT_TICKET_REDIRECT_URI"] = (
    "https://platform.test/api/analytics/google/account-ticket/callback")

import analytics  # noqa: E402
import analytics_consent  # noqa: E402
import analytics_oauth  # noqa: E402
import app as appmod  # noqa: E402
import db  # noqa: E402
import ga4_admin  # noqa: E402
import gtm_admin  # noqa: E402
import orgs as orgs_service  # noqa: E402
import settings  # noqa: E402

TAG = uuid.uuid4().hex[:8]
ORG_NAME = "Consent Client %s" % TAG
ORG_EMAIL = "consent-%s@example.test" % TAG
ORG_PASSWORD = "consent-pass-1234"
MEMBER_EMAIL = "consent-member-%s@example.test" % TAG
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


def cleanup():
    with db.get_connection() as conn:
        conn.execute("DELETE FROM analytics_consents WHERE org_id IN "
                     "(SELECT id FROM orgs WHERE name = ?)", (ORG_NAME,))
        conn.execute("DELETE FROM analytics_actions WHERE org_id IN "
                     "(SELECT id FROM orgs WHERE name = ?)", (ORG_NAME,))
        conn.execute("DELETE FROM analytics_properties WHERE org_id IN "
                     "(SELECT id FROM orgs WHERE name = ?)", (ORG_NAME,))
        conn.execute("DELETE FROM tag_containers WHERE org_id IN "
                     "(SELECT id FROM orgs WHERE name = ?)", (ORG_NAME,))
        conn.execute("DELETE FROM oauth_connections WHERE org_id IN "
                     "(SELECT id FROM orgs WHERE name = ?)", (ORG_NAME,))
        conn.execute("DELETE FROM data_sources WHERE org_id IN "
                     "(SELECT id FROM orgs WHERE name = ?)", (ORG_NAME,))
        conn.execute("DELETE FROM users WHERE org_id IN "
                     "(SELECT id FROM orgs WHERE name = ?)", (ORG_NAME,))
        conn.execute("DELETE FROM orgs WHERE name = ?", (ORG_NAME,))


def setup():
    cleanup()
    db.init_db()
    STATE["org"] = orgs_service.create_org({
        "name": ORG_NAME, "email": ORG_EMAIL, "password": ORG_PASSWORD,
    })
    orgs_service.add_org_user(STATE["org"]["id"], {
        "email": MEMBER_EMAIL, "name": "Consent Member",
        "password": "consent-member-pass", "role": "member",
    })
    analytics_oauth.save_connection(
        STATE["org"]["id"],
        {
            "access_token": "consent-access",
            "refresh_token": "consent-refresh",
            "expires_in": 3600,
            "scope": " ".join(analytics_oauth.READ_SCOPES
                              + analytics_oauth.WRITE_SCOPES
                              + analytics_oauth.BIGQUERY_SCOPES),
        },
        account_email="consent@example.test",
        connection_scope="client",
    )
    STATE["client"] = login(ORG_EMAIL, ORG_PASSWORD)
    STATE["member"] = login(MEMBER_EMAIL, "consent-member-pass")
    settings.set_value("ALLOW_LIVE_ACTIONS", "1", actor="test")


def test_consent_permissions_and_versioning():
    org = STATE["org"]["id"]
    member = STATE["member"].post("/api/analytics/setup/consent", json={
        "accepted": True, "version": analytics_consent.VERSION})
    check("a member cannot grant setup consent", member.status_code, 403)
    wrong = STATE["client"].post("/api/analytics/setup/consent", json={
        "accepted": True, "version": "old-draft"})
    check("an old consent version is refused", wrong.status_code, 409)
    missing = STATE["client"].post("/api/analytics/setup/consent", json={
        "version": analytics_consent.VERSION})
    check("the checkbox must be explicit", missing.status_code, 400)
    granted = STATE["client"].post("/api/analytics/setup/consent", json={
        "accepted": True, "version": analytics_consent.VERSION})
    body = granted.get_json() or {}
    check("consent is created", granted.status_code, 201)
    check("the status says accepted", (body.get("consent") or {}).get("accepted"), True)
    check("autonomy is switched on", body.get("autonomy"), True)
    check("autonomy is stored", settings.get_bool(
        "ANALYTICS_AUTONOMY", False, org_id=org), True)
    check("the grant is audited", any(
        row.get("action") == "analytics_consent_granted"
        for row in analytics.actions(org)), True)

    with db.get_connection() as conn:
        conn.execute("UPDATE analytics_consents SET version = 'older' WHERE org_id = ?",
                     (org,))
    stale = STATE["client"].get("/api/analytics/setup/status").get_json() or {}
    check("a version bump makes the old grant inactive",
          (stale.get("consent") or {}).get("accepted"), False)
    with db.get_connection() as conn:
        conn.execute("UPDATE analytics_consents SET version = ? WHERE org_id = ?",
                     (analytics_consent.VERSION, org))

    revoked = STATE["client"].post("/api/analytics/setup/revoke", json={})
    check("revoke succeeds", revoked.status_code, 200)
    check("revoke clears consent",
          ((revoked.get_json() or {}).get("consent") or {}).get("accepted"), False)
    check("revoke switches autonomy off", settings.get_bool(
        "ANALYTICS_AUTONOMY", True, org_id=org), False)
    print("consent is versioned, admin-only and revocable")


def test_bootstrap_requires_consent():
    reply = STATE["client"].post("/api/analytics/setup/bootstrap", json={
        "account": "accounts/1", "gtm_account_id": "1"})
    check("bootstrap refuses without active consent", reply.status_code, 403)
    check("nothing was created", analytics.properties(STATE["org"]["id"]), [])
    print("one-click setup cannot run without consent")


def test_member_cannot_read_setup_status():
    check("a member cannot read setup status",
          STATE["member"].get("/api/analytics/setup/status").status_code, 403)
    print("setup status is admin-only")



def _fake_google():
    counts = {"property": 0, "stream": 0, "key": 0, "audience": 0, "container": 0}
    state = {"events": set(), "audiences": set(), "measurements": []}
    originals = {
        "create_property": ga4_admin.create_property,
        "create_web_stream": ga4_admin.create_web_stream,
        "list_web_streams": ga4_admin.list_web_streams,
        "create_key_event": ga4_admin.create_key_event,
        "list_key_events": ga4_admin.list_key_events,
        "create_event_audience": ga4_admin.create_event_audience,
        "list_event_audiences": ga4_admin.list_event_audiences,
        "setup_ga4_container": gtm_admin.setup_ga4_container,
    }

    def create_property(token, account, name, tz, currency):
        counts["property"] += 1
        return {"property": "properties/123", "property_id": "123",
                "display_name": name, "account": account, "time_zone": tz,
                "currency_code": currency}

    def create_stream(token, prop, name, uri):
        counts["stream"] += 1
        state["measurements"].append("G-CONSENT")
        return {"stream": "properties/123/dataStreams/456", "stream_id": "456",
                "measurement_id": "G-CONSENT", "default_uri": uri}

    def list_streams(token, prop):
        return [{"stream": "properties/123/dataStreams/456", "stream_id": "456",
                 "measurement_id": "G-CONSENT", "default_uri": "https://example.test"}
                ] if state["measurements"] else []

    def create_key(token, prop, event):
        counts["key"] += 1
        state["events"].add(event)
        return {"key_event": "properties/123/keyEvents/1", "event_name": event,
                "counting_method": "ONCE_PER_EVENT"}

    def list_keys(token, prop):
        return [{"key_event": "properties/123/keyEvents/1", "event_name": event}
                for event in state["events"]]

    def create_audience(token, prop, name, event, days):
        counts["audience"] += 1
        state["audiences"].add(name)
        return {"audience": "properties/123/audiences/1", "display_name": name,
                "event_name": event}

    def list_audiences(token, prop):
        return [{"audience": "properties/123/audiences/1", "display_name": name}
                for name in state["audiences"]]

    def create_container(token, account_id, name, domain, measurement, events):
        counts["container"] += 1
        return {"container_id": "10", "public_id": "GTM-CONSENT", "name": name,
                "version_id": "5", "account_id": account_id}

    ga4_admin.create_property = create_property
    ga4_admin.create_web_stream = create_stream
    ga4_admin.list_web_streams = list_streams
    ga4_admin.create_key_event = create_key
    ga4_admin.list_key_events = list_keys
    ga4_admin.create_event_audience = create_audience
    ga4_admin.list_event_audiences = list_audiences
    gtm_admin.setup_ga4_container = create_container

    def restore():
        for name, value in originals.items():
            if name == "setup_ga4_container":
                gtm_admin.setup_ga4_container = value
            else:
                setattr(ga4_admin, name, value)
    return counts, restore



def test_bootstrap_is_idempotent():
    org = STATE["org"]["id"]
    STATE["client"].post("/api/analytics/setup/consent", json={
        "accepted": True, "version": analytics_consent.VERSION})
    counts, restore = _fake_google()
    try:
        payload = {
            "connection_scope": "client", "account": "accounts/1",
            "account_name": "Consent account", "property_name": "Consent site",
            "gtm_account_id": "1", "container_name": "Consent site",
            "key_events": "form_submit", "audience_event": "form_submit",
            "make_container": True,
        }
        first = STATE["client"].post("/api/analytics/setup/bootstrap", json=payload)
        second = STATE["client"].post("/api/analytics/setup/bootstrap", json=payload)
    finally:
        restore()
    first_body = first.get_json() or {}
    second_body = second.get_json() or {}
    check("the first bootstrap succeeds", first.status_code, 200)
    check("the second bootstrap succeeds", second.status_code, 200)
    check("the first run creates one property", counts["property"], 1)
    check("the first run creates one stream", counts["stream"], 1)
    check("the first run creates one key event", counts["key"], 1)
    check("the first run creates one audience", counts["audience"], 1)
    check("the first run creates one container", counts["container"], 1)
    check("the second run reuses the property", second_body.get("mode"), "existing")
    check("the second run creates no property", counts["property"], 1)
    check("the second run creates no container", counts["container"], 1)
    check("only one property row exists", len(analytics.properties(org)), 1)
    check("only one container row exists", len(analytics.containers(org)), 1)
    print("one-click setup creates once and reuses everything on the second click")



def test_no_account_returns_needs_instead_of_dead_end():
    org = STATE["org"]["id"]
    with db.get_connection() as conn:
        conn.execute("DELETE FROM analytics_properties WHERE org_id = ?", (org,))
        conn.execute("DELETE FROM tag_containers WHERE org_id = ?", (org,))
        conn.execute("DELETE FROM data_sources WHERE org_id = ?", (org,))
    original = ga4_admin.list_accounts
    ga4_admin.list_accounts = lambda token: []
    try:
        reply = STATE["client"].post("/api/analytics/setup/bootstrap", json={
            "connection_scope": "client", "make_container": False})
    finally:
        ga4_admin.list_accounts = original
    body = reply.get_json() or {}
    check("no-account bootstrap returns guidance", reply.status_code, 200)
    check("no-account bootstrap names the missing account",
          body.get("needs"), ["ga4_account"])
    check("no-account bootstrap creates no property", analytics.properties(org), [])
    check("the guidance is not a dead-end toast", "Create one" in
          str(body.get("error") or ""), True)
    print("a missing Analytics account returns the ticket path, not an error toast")


def test_account_ticket_payload_and_callback():
    calls = []

    def fake_transport(url, headers=None, timeout=20, method="GET", payload=None):
        calls.append({"url": url, "method": method, "payload": payload})
        if method == "GET" and "/accounts?pageSize=200" in url:
            return 200, '{"accounts": []}'
        if method == "POST" and url.endswith("/accounts:provisionAccountTicket"):
            return 200, '{"accountTicketId": "ticket-123"}'
        return 404, "{}"

    original = ga4_admin._transport
    ga4_admin._transport = fake_transport
    try:
        status = STATE["client"].get(
            "/api/analytics/setup/status?connection_scope=client&discover=1")
        status_body = status.get_json() or {}
        check("setup status can discover accounts", status.status_code, 200)
        check("the empty Analytics account list is exposed",
              status_body.get("ga_accounts"), [])
        check("the setup card can see the ticket redirect",
              bool(status_body.get("account_ticket_redirect_uri")), True)
        reply = STATE["client"].post("/api/analytics/google/account-ticket", json={
            "connection_scope": "client", "display_name": "Consent account",
            "auto_setup": True})
        check("the account ticket is created", reply.status_code, 201)
        body = reply.get_json() or {}
        check("the ticket id is returned", body.get("account_ticket_id"), "ticket-123")
        check("the ToS URL carries the ticket",
              "ticket-123" in str(body.get("tos_url") or ""), True)
        create = [row for row in calls
                  if row["method"] == "POST" and ":provisionAccountTicket" in row["url"]]
        check("provisionAccountTicket was called once", len(create), 1)
        check("the account payload is sent",
              ((create[0] if create else {}).get("payload") or {}).get("account"),
              {"displayName": "Consent account"})
        check("the registered redirect URI is sent",
              ((create[0] if create else {}).get("payload") or {}).get("redirectUri"),
              "https://platform.test/api/analytics/google/account-ticket/callback")
        callback = STATE["client"].get(
            "/api/analytics/google/account-ticket/callback")
        check("the callback returns to the console", callback.status_code, 302)
        check("the callback lands on Analytics",
              "/admin/#/app/analytics" in (callback.headers.get("Location") or ""), True)
        notice = STATE["client"].get("/api/analytics/oauth/notice").get_json() or {}
        check("the account callback remembers auto setup",
              (notice.get("notice") or {}).get("auto_setup"), True)
    finally:
        ga4_admin._transport = original
    print("Google account creation uses a ticket and returns through the callback")


def main():
    setup()
    test_consent_permissions_and_versioning()
    test_bootstrap_requires_consent()
    test_bootstrap_is_idempotent()
    test_no_account_returns_needs_instead_of_dead_end()
    test_account_ticket_payload_and_callback()
    test_member_cannot_read_setup_status()
    settings.clear_value("ALLOW_LIVE_ACTIONS", actor="test")
    settings.clear_value("ANALYTICS_AUTONOMY", actor="test",
                         org_id=STATE["org"]["id"])
    if FAILED:
        print("\n%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
        return 1
    print("\nANALYTICS CONSENT OK - consent, account ticket and idempotent setup are wired")
    return 0


if __name__ == "__main__":
    sys.exit(main())
