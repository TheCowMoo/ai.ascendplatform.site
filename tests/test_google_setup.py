"""Phase 2 Google setup: GA4 writes, GTM provisioning, tag injection and audit.

Run: .\\venv\\Scripts\\python.exe tests\\test_google_setup.py

No Google network call is made. The admin transports are replaced with fakes that record
the method and payload, so the test pins the resource shapes we send.
"""

import json
import os
import sys
import tempfile
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="google-setup-"), "test.db").replace("\\", "/")
os.environ.setdefault("NOTIFICATIONS_ENABLED", "0")
os.environ.setdefault("OAUTH_TOKEN_KEY", "test-google-setup-key")

import analytics  # noqa: E402
import analytics_oauth  # noqa: E402
import analytics_setup  # noqa: E402
import asyncio  # noqa: E402
import actions  # noqa: E402
import app as appmod  # noqa: E402
import db  # noqa: E402
import forms_page  # noqa: E402
import ga4_admin  # noqa: E402
import gtm_admin  # noqa: E402
import jobs  # noqa: E402
import manager as manager_mod  # noqa: E402
import orgs as orgs_service  # noqa: E402
import provisioner  # noqa: E402
import settings  # noqa: E402
import sites as sites_service  # noqa: E402
import tag_install  # noqa: E402
import tools as tool_registry  # noqa: E402
import tools.manager as manager_tools  # noqa: E402

TAG = uuid.uuid4().hex[:8]
ORG_NAME = "Google Setup %s" % TAG
ORG_EMAIL = "google-setup-%s@example.test" % TAG
ORG_PASSWORD = "google-setup-pass-1234"
STATE = {}
FAILED = []


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name +
          ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


def json_body(call):
    return call.get("payload") or {}


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
            "access_token": "setup-access",
            "refresh_token": "setup-refresh",
            "expires_in": 3600,
            "scope": " ".join(analytics_oauth.READ_SCOPES + analytics_oauth.WRITE_SCOPES),
        },
        account_email="setup@example.test",
        connection_scope="client",
    )
    STATE["client"] = login(ORG_EMAIL, ORG_PASSWORD)
    with db.get_connection() as conn:
        user_id = conn.execute("SELECT id FROM users WHERE email = ?",
                               (ORG_EMAIL,)).fetchone()["id"]
    STATE["job"] = jobs.create_job(
        "Google setup", "test the analytics setup tool", user_id, "chat",
        org_id=STATE["org"]["id"])


def test_ga4_write_calls():
    calls = []

    def fake(url, headers=None, timeout=20, method="GET", payload=None):
        calls.append({"url": url, "method": method, "payload": payload})
        if method == "POST" and url.endswith("/properties"):
            return 200, json.dumps({
                "name": "properties/123", "displayName": "Test property",
                "account": "accounts/1", "timeZone": "Etc/UTC",
                "currencyCode": "USD",
            })
        if method == "POST" and "/dataStreams" in url:
            return 200, json.dumps({
                "name": "properties/123/dataStreams/456",
                "webStreamData": {"measurementId": "G-TEST123",
                                  "defaultUri": "https://example.test"},
            })
        if method == "POST" and "/keyEvents" in url:
            return 200, json.dumps({"name": "properties/123/keyEvents/1",
                                    "eventName": payload.get("eventName"),
                                    "countingMethod": "ONCE_PER_EVENT"})
        if method == "POST" and "/audiences" in url:
            return 200, json.dumps({"name": "properties/123/audiences/1",
                                    "displayName": payload.get("displayName")})
        return 404, "{}"

    original = ga4_admin._transport
    ga4_admin._transport = fake
    try:
        prop = ga4_admin.create_property(
            "token", "accounts/1", "Test property", "Etc/UTC", "USD")
        stream = ga4_admin.create_web_stream(
            "token", "properties/123", "Website", "https://example.test")
        key = ga4_admin.create_key_event("token", "properties/123", "form_submit")
        audience = ga4_admin.create_event_audience(
            "token", "properties/123", "Form submitters", "form_submit", 30)
    finally:
        ga4_admin._transport = original

    check("property id is returned", prop.get("property_id"), "123")
    check("stream measurement id is returned",
          stream.get("measurement_id"), "G-TEST123")
    check("key event is returned", key.get("event_name"), "form_submit")
    check("audience is returned", audience.get("display_name"), "Form submitters")

    create = [c for c in calls if c["url"].endswith("/properties")][0]
    check("property create is a POST", create["method"], "POST")
    check("property parent is the account",
          json_body(create).get("parent"), "accounts/1")
    check("property display name", json_body(create).get("displayName"),
          "Test property")
    stream_call = [c for c in calls if "/dataStreams" in c["url"]][0]
    check("stream is a web stream", json_body(stream_call).get("type"),
          "WEB_DATA_STREAM")
    check("stream default uri",
          (json_body(stream_call).get("webStreamData") or {}).get("defaultUri"),
          "https://example.test")
    key_call = [c for c in calls if "/keyEvents" in c["url"]][0]
    check("key event name", json_body(key_call).get("eventName"), "form_submit")
    audience_call = [c for c in calls if "/audiences" in c["url"]][0]
    clauses = json_body(audience_call).get("filterClauses") or []
    check("audience has one include clause", len(clauses), 1)
    check("audience clause includes",
          (clauses[0] if clauses else {}).get("clauseType"), "INCLUDE")
    simple = (clauses[0] if clauses else {}).get("simpleFilter") or {}
    check("audience scope is one event", simple.get("scope"),
          "AUDIENCE_FILTER_SCOPE_WITHIN_SAME_EVENT")
    expressions = ((simple.get("filterExpression") or {}).get("andGroup") or {}) \
        .get("filterExpressions") or []
    first = expressions[0] if expressions else {}
    check("the top-level andGroup contains an orGroup",
          "orGroup" in first, True)
    check("eventFilter is not a direct child of andGroup",
          "eventFilter" in first, False)
    or_expressions = (first.get("orGroup") or {}).get("filterExpressions") or []
    check("audience filters the event inside the orGroup",
          ((or_expressions[0] if or_expressions else {}).get("eventFilter") or {})
          .get("eventName"), "form_submit")
    check("audience uses the alpha endpoint", "/v1alpha/" in audience_call["url"], True)
    print("GA4 property, stream, key event and audience payloads are wired")


def test_gtm_setup():
    calls = []
    counters = {"trigger": 0, "tag": 0}

    def fake(url, headers=None, timeout=20, method="GET", payload=None):
        calls.append({"url": url, "method": method, "payload": payload})
        if method == "POST" and url.endswith("/containers"):
            return 200, json.dumps({
                "containerId": "10", "publicId": "GTM-ABC123",
                "name": payload.get("name"), "domainName": payload.get("domainName"),
            })
        if method == "GET" and url.endswith("/workspaces"):
            return 200, json.dumps({"workspace": [{
                "workspaceId": "1", "name": "Default Workspace"}]})
        if method == "POST" and url.endswith("/triggers"):
            counters["trigger"] += 1
            return 200, json.dumps({"triggerId": str(counters["trigger"]),
                                    "name": payload.get("name")})
        if method == "POST" and url.endswith("/tags"):
            counters["tag"] += 1
            return 200, json.dumps({"tagId": str(counters["tag"]),
                                    "name": payload.get("name")})
        if method == "POST" and ":create_version" in url:
            return 200, json.dumps({"containerVersion": {
                "containerVersionId": "5", "name": payload.get("name")}})
        if method == "POST" and ":publish" in url:
            return 200, json.dumps({"containerVersion": {
                "containerVersionId": "5", "name": "AscendPlatform setup"}})
        return 404, "{}"

    original = gtm_admin._transport
    gtm_admin._transport = fake
    try:
        result = gtm_admin.setup_ga4_container(
            "token", "1", "Example site", "example.test", "G-TEST123",
            ("form_submit",))
    finally:
        gtm_admin._transport = original

    check("container public id", result.get("public_id"), "GTM-ABC123")
    check("container version", result.get("version_id"), "5")
    container_call = [c for c in calls if c["url"].endswith("/containers")][0]
    check("container is a web container",
          json_body(container_call).get("usageContext"), ["web"])
    check("container domain",
          json_body(container_call).get("domainName"), ["example.test"])
    triggers = [c for c in calls if c["url"].endswith("/triggers")]
    check("two triggers were created", len(triggers), 2)
    check("all-pages trigger type", json_body(triggers[0]).get("type"), "pageview")
    check("form trigger type", json_body(triggers[1]).get("type"), "customEvent")
    custom = (json_body(triggers[1]).get("customEventFilter") or [{}])[0]
    args = custom.get("parameter") or []
    check("custom event compares _event", args[0].get("value"), "{{_event}}")
    check("custom event compares form_submit", args[1].get("value"), "form_submit")
    tags = [c for c in calls if c["url"].endswith("/tags")]
    check("two tags were created", len(tags), 2)
    check("GA4 config tag type", json_body(tags[0]).get("type"), "gaawc")
    check("GA4 event tag type", json_body(tags[1]).get("type"), "gaawe")
    config_params = {p.get("key"): p.get("value")
                     for p in json_body(tags[0]).get("parameter") or []}
    event_params = {p.get("key"): p.get("value")
                    for p in json_body(tags[1]).get("parameter") or []}
    check("config tag has the measurement id",
          config_params.get("measurementId"), "G-TEST123")
    check("event tag has the event name", event_params.get("eventName"), "form_submit")
    check("config fires on all pages",
          json_body(tags[0]).get("firingTriggerId"), ["1"])
    check("event fires on the custom trigger",
          json_body(tags[1]).get("firingTriggerId"), ["2"])
    check("a version was published",
          any(":publish" in c["url"] for c in calls), True)
    publish_call = [c for c in calls if ":publish" in c["url"]][0]
    check("publish sends no body", publish_call.get("payload"), None)
    print("GTM container, GA4 tags, triggers, version and publish are wired")


def test_tag_injection():
    html = ("<!doctype html><html><head><title>Example</title></head>"
            "<body><h1>Hello</h1></body></html>")
    once = tag_install.inject(html, "GTM-ABC123")
    twice = tag_install.inject(once, "GTM-ABC123")
    check("the container marker is present once",
          once.count("<!-- GTM:GTM-ABC123 -->"), 1)
    check("the loader is present once", once.count("gtm.js?id="), 1)
    check("a second inject is a no-op", twice, once)
    check("the head script is before </head>",
          once.index("gtm.js") < once.index("</head>"), True)
    check("the body tag is after <body>",
          once.index("<noscript>") > once.index("<body"), True)
    check("the body tag is before </body>",
          once.index("<noscript>") < once.index("</body>"), True)
    check("no container means no change", tag_install.inject(html, ""), html)
    print("the GTM snippet is injected idempotently into head and body")


class FakeTarget:
    name = "local"
    is_remote = False

    def __init__(self):
        self.files = {}

    def read_file(self, path, limit=200000):
        return self.files.get(path, "")

    def write_file(self, path, content, job_id=None, emit=None, summary=None,
                   preapproved=False):
        self.files[path] = content
        return {"ok": True, "dry_run": False, "output": "wrote"}


def test_install_for_site():
    site = sites_service.create_site({
        "domain": "tag-%s.example.test" % TAG,
        "target": "local",
        "content_html": ("<html><head><title>Tag site</title></head>"
                         "<body><h1>Hello</h1></body></html>"),
    }, org_id=STATE["org"]["id"])
    docroot = tempfile.mkdtemp(prefix="tag-site-")
    target = FakeTarget()
    original = provisioner.target_docroot
    provisioner.target_docroot = lambda row: (target, docroot)
    try:
        first = tag_install.install_for_site(site, "GTM-ABC123")
        fresh = sites_service.get_site(site["id"])
        second = tag_install.install_for_site(fresh, "GTM-ABC123")
    finally:
        provisioner.target_docroot = original
    path = provisioner.join_path(docroot, "index.html")
    check("the home page was written", path in target.files, True)
    check("the home page has the container",
          "GTM-ABC123" in target.files.get(path, ""), True)
    check("the home page was updated", first.get("updated"), 1)
    check("the site row now carries the snippet",
          "GTM-ABC123" in (sites_service.get_site(site["id"]).get("content_html") or ""),
          True)
    check("a second install changes nothing", second.get("updated"), 0)
    print("install_for_site writes and remembers the tag on the live home page")


def test_analytics_setup_writes_records():
    org = STATE["org"]["id"]
    connection = analytics_oauth.get_connection(org, "client")
    calls = []

    originals = {
        "create_property": ga4_admin.create_property,
        "create_web_stream": ga4_admin.create_web_stream,
        "create_key_event": ga4_admin.create_key_event,
        "create_event_audience": ga4_admin.create_event_audience,
        "setup_ga4_container": gtm_admin.setup_ga4_container,
    }

    ga4_admin.create_property = lambda token, account, name, tz, currency: {
        "property": "properties/123", "property_id": "123",
        "display_name": name, "account": account, "time_zone": tz,
        "currency_code": currency,
    }
    ga4_admin.create_web_stream = lambda token, prop, name, uri: {
        "stream": "properties/123/dataStreams/456", "stream_id": "456",
        "measurement_id": "G-TEST123", "default_uri": uri,
    }
    ga4_admin.create_key_event = lambda token, prop, event: {
        "key_event": "properties/123/keyEvents/1", "event_name": event,
        "counting_method": "ONCE_PER_EVENT",
    }
    ga4_admin.create_event_audience = lambda token, prop, name, event, days: {
        "audience": "properties/123/audiences/1", "display_name": name,
        "event_name": event,
    }

    def fake_container(token, account_id, name, domain, measurement, events):
        calls.append({"account_id": account_id, "name": name, "domain": domain,
                      "measurement": measurement, "events": events})
        return {"container_id": "10", "public_id": "GTM-ABC123", "name": name,
                "version_id": "5", "account_id": account_id}

    gtm_admin.setup_ga4_container = fake_container
    try:
        result = analytics_setup.setup(
            org, "client", "setup-access", connection, "tester",
            {
                "account": "accounts/1",
                "account_name": "Test account",
                "property_name": "Test property",
                "time_zone": "Etc/UTC",
                "currency_code": "USD",
                "key_events": "form_submit, generate_lead",
                "audience_event": "form_submit",
                "make_container": True,
                "gtm_account_id": "1",
                "container_name": "Test container",
            },
            None)
    finally:
        ga4_admin.create_property = originals["create_property"]
        ga4_admin.create_web_stream = originals["create_web_stream"]
        ga4_admin.create_key_event = originals["create_key_event"]
        ga4_admin.create_event_audience = originals["create_event_audience"]
        gtm_admin.setup_ga4_container = originals["setup_ga4_container"]

    check("setup reports success", result.get("ok"), True)
    props = analytics.properties(org)
    check("the property row exists", len(props), 1)
    check("the property id is stored",
          (props[0] if props else {}).get("property_id"), "123")
    containers = analytics.containers(org)
    check("the container row exists", len(containers), 1)
    check("the container public id is stored",
          (containers[0] if containers else {}).get("public_id"), "GTM-ABC123")
    check("the property points at the container",
          (props[0] if props else {}).get("tag_container_id"),
          (containers[0] if containers else {}).get("id"))
    sources = analytics.sources(org)
    check("the source row exists", len(sources), 1)
    check("the source is marked OAuth",
          (sources[0] if sources else {}).get("credential_ref"),
          "oauth:%s" % connection["id"])
    check("the container call got the measurement id",
          (calls[0] if calls else {}).get("measurement"), "G-TEST123")
    check("the container call got both events",
          (calls[0] if calls else {}).get("events"),
          ("form_submit", "generate_lead"))
    action_names = {row.get("action") for row in analytics.actions(org)}
    for name in ("ga4_create_property", "ga4_create_stream", "ga4_create_key_event",
                 "ga4_create_audience", "gtm_create_container"):
        check("audit recorded %s" % name, name in action_names, True)
    print("setup stores the source/property/container and records every write")


def test_form_page_tag():
    html = forms_page.page(
        {"id": 7, "name": "Demo form", "settings": {}},
        {"questions": [], "progress": {"step": 0, "total": 0}},
        "/step", "/submit", gtm_id="GTM-ABC123")
    check("the form page carries the container", "GTM-ABC123" in html, True)
    check("the form page pushes form_submit", "event: 'form_submit'" in html, True)
    check("the push carries the form id", "STATE.form_id" in html, True)
    plain = forms_page.page(
        {"id": 7, "name": "Demo form", "settings": {}},
        {"questions": [], "progress": {"step": 0, "total": 0}},
        "/step", "/submit")
    check("no container means no snippet", "GTM-" not in plain, True)
    print("the hosted form page carries the container and pushes form_submit")


def test_write_scope_guard_and_tool_registry():
    org = STATE["org"]["id"]
    token, connection = analytics_oauth.write_token_for_org(org, need_gtm=True)
    check("the write token is the saved access token", token, "setup-access")
    original = connection["scope"]
    with db.get_connection() as conn:
        conn.execute("UPDATE oauth_connections SET scope = ? WHERE id = ?",
                     (" ".join(analytics_oauth.READ_SCOPES), connection["id"]))
    try:
        try:
            analytics_oauth.write_token_for_org(org, need_gtm=True)
            message = ""
        except analytics_oauth.OAuthError as exc:
            message = str(exc)
    finally:
        with db.get_connection() as conn:
            conn.execute("UPDATE oauth_connections SET scope = ? WHERE id = ?",
                         (original, connection["id"]))
    check("a read-only grant is refused for setup", "publish" in message, True)
    check("the manager may run setup", "setup_analytics" in manager_mod.GRANTED, True)
    check("account discovery is a read tool",
          "list_google_accounts" in tool_registry.READ_ONLY_TOOLS, True)
    check("setup is not a read tool",
          "setup_analytics" not in tool_registry.READ_ONLY_TOOLS, True)
    print("write scopes are enforced and the setup tool is registered")


def test_ai_setup_approval_and_autonomy():
    org = STATE["org"]["id"]
    job = STATE["job"]
    calls = []
    original_setup = analytics_setup.setup

    def fake_setup(org_id, scope, token, connection, actor, data, site=None):
        calls.append({"org_id": org_id, "scope": scope, "property_name":
                      data.get("property_name"), "make_container":
                      data.get("make_container")})
        return {
            "ok": True,
            "property": {"property_id": "123",
                         "display_name": data.get("property_name")},
            "container": ({"public_id": "GTM-AI"} if data.get("make_container")
                          else None),
            "tag": None, "source": {}, "stream": {}, "key_events": [],
            "audience": None, "errors": [],
        }

    try:
        settings.set_value("ALLOW_LIVE_ACTIONS", "1", actor="test")
        settings.set_value("ANALYTICS_AUTONOMY", "0", actor="test", org_id=org)
        analytics_setup.setup = fake_setup
        reply = asyncio.run(manager_tools._build_setup_analytics(None, job_id=job)(
            account="accounts/1", property_name="AI property",
            make_container=False))
        check("autonomy off asks for approval", "approval #" in reply.lower(), True)
        check("autonomy off does not write", calls, [])
        pending = [row for row in actions.list_approvals("pending", org_id=org)
                   if row.get("tool") == "analytics_setup"]
        check("the approval is in the inbox", len(pending), 1)
        approval_id = pending[0]["id"] if pending else 0
        response = STATE["client"].post(
            "/api/approvals/%d/approve" % approval_id)
        check("approval executes the setup", response.status_code, 200)
        check("approval ran setup once", len(calls), 1)
        check("approval is marked approved",
              actions.get_approval(approval_id).get("status"), "approved")

        settings.set_value("ANALYTICS_AUTONOMY", "1", actor="test", org_id=org)
        reply = asyncio.run(manager_tools._build_setup_analytics(None, job_id=job)(
            account="accounts/1", property_name="Autonomous property",
            make_container=False))
        check("autonomy on writes directly", len(calls), 2)
        check("autonomy on reports success", "Created GA4 property" in reply, True)
    finally:
        analytics_setup.setup = original_setup
        settings.clear_value("ALLOW_LIVE_ACTIONS", actor="test")
        settings.clear_value("ANALYTICS_AUTONOMY", actor="test", org_id=org)
    print("AI setup drafts an approval when autonomy is off and writes when it is on")


def main():
    setup()
    test_ga4_write_calls()
    test_gtm_setup()
    test_tag_injection()
    test_install_for_site()
    test_analytics_setup_writes_records()
    test_form_page_tag()
    test_write_scope_guard_and_tool_registry()
    test_ai_setup_approval_and_autonomy()
    if FAILED:
        print("\n%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
        return 1
    print("\nGOOGLE SETUP OK - GA4/GTM writes, snippet installation and audit are wired")
    return 0


if __name__ == "__main__":
    sys.exit(main())


