"""Analytics intelligence: insights, alert rules, dedupe, email and routes.

Run: .\\venv\\Scripts\\python.exe tests\\test_alerts.py
"""

import datetime
import os
import sys
import tempfile
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="alerts-"), "test.db").replace("\\", "/")
os.environ.setdefault("NOTIFICATIONS_ENABLED", "0")
os.environ.setdefault("OAUTH_TOKEN_KEY", "test-alerts-key")

import analytics  # noqa: E402
import analytics_alerts  # noqa: E402
import app as appmod  # noqa: E402
import db  # noqa: E402
import jobs  # noqa: E402
import manager as manager_mod  # noqa: E402
import notifications  # noqa: E402
import orgs as orgs_service  # noqa: E402
import scheduler  # noqa: E402
import settings  # noqa: E402
import tools as tool_registry  # noqa: E402

TAG = uuid.uuid4().hex[:8]
ORG_NAME = "Alerts Client %s" % TAG
ORG_EMAIL = "alerts-%s@example.test" % TAG
ORG_PASSWORD = "alerts-pass-1234"
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


def day(offset):
    return (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=offset)).strftime("%Y-%m-%d")


def setup():
    db.init_db()
    STATE["org"] = orgs_service.create_org({
        "name": ORG_NAME, "email": ORG_EMAIL, "password": ORG_PASSWORD,
    })
    org_id = STATE["org"]["id"]
    analytics.save_source({
        "kind": "caddy", "external_id": "", "label": "Site logs",
    }, org_id=org_id)
    points = []
    # Current window: 40 pageviews / 30 sessions / 10 errors.
    for offset, views, sessions, errors in (
            (1, 15, 10, 3), (2, 10, 8, 3), (3, 15, 12, 4)):
        points.extend([
            ("pageviews", "", day(offset), views),
            ("sessions", "", day(offset), sessions),
            ("errors", "", day(offset), errors),
            ("page", "/pricing", day(offset), views),
        ])
    # Previous window: 100 pageviews / 50 sessions / 1 error.
    for offset, views, sessions, errors in (
            (8, 50, 25, 1), (9, 50, 25, 0)):
        points.extend([
            ("pageviews", "", day(offset), views),
            ("sessions", "", day(offset), sessions),
            ("errors", "", day(offset), errors),
            ("page", "/pricing", day(offset), views),
        ])
    analytics.record_metrics(org_id, "caddy", points)
    STATE["client"] = login(ORG_EMAIL, ORG_PASSWORD)


def test_insights_and_defaults():
    data = analytics_alerts.insights(STATE["org"]["id"], days=7)
    titles = [item.get("title") for item in data.get("insights") or []]
    check("pageviews insight is present",
          any("Pageviews down" in title for title in titles), True)
    check("errors insight is present",
          any("Errors up" in title for title in titles), True)
    check("the top page is present",
          any("Top page:" in title for title in titles), True)
    pageviews = [item for item in data["insights"]
                 if item.get("key") == "pageviews"][0]
    check("pageviews change is -60%", pageviews.get("change_pct"), -60.0)
    rules = analytics_alerts.ensure_default_rules(STATE["org"]["id"])
    check("three default rules are created", len(rules), 3)
    check("defaults do not email", all(rule.get("email") == 0 for rule in rules), True)
    check("the manager has get_insights", "get_insights" in manager_mod.GRANTED, True)
    check("get_insights is read-only",
          "get_insights" in tool_registry.READ_ONLY_TOOLS, True)
    print("insights explain the change and defaults are in-app only")


def test_evaluate_dedupe_and_email():
    org = STATE["org"]["id"]
    first = analytics_alerts.evaluate(org, notify=False)
    check("all three defaults fire", first.get("alerts"), 3)
    second = analytics_alerts.evaluate(org, notify=False)
    check("a second run does not duplicate", second.get("alerts"), 0)
    open_alerts = analytics_alerts.list_alerts(org, status="open")
    check("the open inbox has three alerts", len(open_alerts), 3)
    check("the alert carries a rule name", bool(open_alerts[0].get("rule_name")), True)
    marked = analytics_alerts.mark_alert(org, open_alerts[0]["id"], "read")
    check("an alert can be marked read", marked.get("status"), "read")

    # Add one email rule. The evaluate call is dry-run by default, so the alert is stored
    # but no message goes out; enabling live actions lets the fake mailer see it.
    rule = analytics_alerts.save_rule(org, {
        "name": "Email pageview drop", "metric": "pageviews",
        "comparison": "drop", "threshold": 5, "window_days": 7,
        "email": True, "enabled": True,
    })
    check("the custom rule is stored", bool(rule.get("id")), True)
    original_notify = notifications.notify_org
    sent = []
    notifications.notify_org = lambda org_id, subject, message: (
        sent.append(subject) or {"ok": True})
    settings.set_value("ALLOW_LIVE_ACTIONS", "1", actor="test")
    try:
        result = analytics_alerts.evaluate(org, notify=True)
    finally:
        notifications.notify_org = original_notify
        settings.clear_value("ALLOW_LIVE_ACTIONS", actor="test")
    check("the email rule creates an alert", result.get("alerts"), 1)
    check("the email rule sends one message", len(sent), 1)
    check("the email names the alert", "Analytics alert:" in sent[0], True)
    print("evaluation dedupes per week and respects the dry-run mail switch")


def test_routes():
    client = STATE["client"]
    response = client.get("/api/analytics/insights?days=7")
    check("the insights route returns 200", response.status_code, 200)
    check("the insights route has insights",
          bool((response.get_json() or {}).get("insights")), True)
    response = client.get("/api/analytics/alerts?status=open")
    check("the alerts route returns 200", response.status_code, 200)
    check("the alert inbox is readable",
          isinstance((response.get_json() or {}).get("alerts"), list), True)
    response = client.get("/api/analytics/alert-rules")
    check("the rules route returns 200", response.status_code, 200)
    response = client.post("/api/analytics/alert-rules", json={
        "name": "Route rule", "metric": "sessions", "comparison": "drop",
        "threshold": 25, "window_days": 14, "email": False,
    })
    check("a rule can be created through the API", response.status_code, 201)
    rule_id = (response.get_json() or {}).get("rule", {}).get("id")
    check("the route rule has an id", bool(rule_id), True)
    response = client.delete("/api/analytics/alert-rules/%s" % rule_id)
    check("a rule can be deleted through the API", response.status_code, 200)
    response = client.post("/api/analytics/alert-rules/defaults")
    check("defaults can be restored through the API", response.status_code, 201)
    response = client.post("/api/analytics/alerts/evaluate", json={"days": 7})
    check("alerts can be evaluated through the API", response.status_code, 200)
    alerts = (client.get("/api/analytics/alerts").get_json() or {}).get("alerts") or []
    if alerts:
        response = client.post("/api/analytics/alerts/%s/read" % alerts[0]["id"])
        check("an alert can be marked read through the API", response.status_code, 200)
    print("insights, alerts and rule routes are wired")


def test_scheduler_queue():
    first = scheduler.queue_alerts()
    second = scheduler.queue_alerts()
    check("the scheduler queues one alert run", first, 1)
    check("a second hourly tick does not queue another", second, 0)
    print("the scheduler dedupes alert jobs per client")


def test_disabled_retention_and_rate_limit():
    org = STATE["org"]["id"]
    settings.set_value("ANALYTICS_ALERTS_ENABLED", "0", actor="test", org_id=org)
    try:
        check("defaults respect the client switch",
              analytics_alerts.ensure_default_rules(org), [])
        disabled = analytics_alerts.evaluate(org)
        check("evaluation respects the client switch", disabled.get("disabled"), True)
        response = STATE["client"].post("/api/analytics/alerts/evaluate", json={})
        check("the evaluate route reports the switch", response.status_code, 409)
    finally:
        settings.clear_value("ANALYTICS_ALERTS_ENABLED", actor="test", org_id=org)

    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO analytics_actions (org_id, action, status, created_at) "
            "VALUES (?, 'old_action', 'ok', '2000-01-01 00:00:00')", (org,))
        conn.execute(
            "INSERT INTO analytics_alerts (org_id, rule_id, metric, title, detail, "
            "period_key, status, created_at) "
            "VALUES (?, NULL, 'pageviews', 'old read', 'old', 'old-read', 'read', "
            "'2000-01-01 00:00:00')", (org,))
        conn.execute(
            "INSERT INTO analytics_alerts (org_id, rule_id, metric, title, detail, "
            "period_key, status, created_at) "
            "VALUES (?, NULL, 'pageviews', 'old open', 'old', 'old-open', 'open', "
            "'2000-01-01 00:00:00')", (org,))
    check("old actions are pruned", analytics.prune_actions(180), 1)
    check("old handled alerts are pruned",
          analytics_alerts.prune_alerts(180), 1)
    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT title FROM analytics_alerts WHERE title IN ('old read', 'old open')"
        ).fetchall()
    check("an old open alert is kept", [row["title"] for row in rows], ["old open"])

    settings.set_value("ALLOW_LIVE_ACTIONS", "1", actor="test")
    try:
        for _ in range(3):
            analytics.record_action(org, "ga4_create_property", actor="test")
        response = STATE["client"].post("/api/analytics/google/setup", json={
            "connection_scope": "client", "account": "accounts/1",
            "property_name": "Blocked property", "make_container": False,
        })
    finally:
        settings.clear_value("ALLOW_LIVE_ACTIONS", actor="test")
    check("the human setup route has a rate limit", response.status_code, 429)
    print("alert switch, retention and the human setup rate limit are enforced")


def test_a_bad_rule_does_not_stop_the_rest():
    org = STATE["org"]["id"]
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO analytics_alert_rules "
            "(org_id, name, metric, comparison, threshold, window_days, enabled, email) "
            "VALUES (?, 'Broken rule', 'pageviews', 'drop', 10, 'not-a-number', 1, 0)",
            (org,))
    result = analytics_alerts.evaluate(org, notify=False)
    check("a bad rule is reported", bool(result.get("errors")), True)
    check("the other rules still run", isinstance(result.get("items"), list), True)
    print("one malformed rule cannot stop the rest of the alert run")


def main():
    setup()
    test_insights_and_defaults()
    test_evaluate_dedupe_and_email()
    test_routes()
    test_scheduler_queue()
    test_disabled_retention_and_rate_limit()
    test_a_bad_rule_does_not_stop_the_rest()
    if FAILED:
        print("\n%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
        return 1
    print("\nALERTS OK - insights, rules, dedupe, email and routes are wired")
    return 0


if __name__ == "__main__":
    sys.exit(main())

