"""Period-over-period intelligence and alert rules for analytics.

The intelligence is deliberately explainable: every insight names the metric, the two
windows and the percentage change. Rules are per client and produce one alert per rule per
ISO week, whether evaluated by the scheduler or by an explicit click.
"""

import datetime

import analytics
import notifications
import review
import settings

METRICS = ("pageviews", "sessions", "errors")
METRIC_LABELS = {
    "pageviews": "Pageviews",
    "sessions": "Sessions",
    "errors": "Errors",
}
DEFAULTS = (
    {"name": "Pageviews falling", "metric": "pageviews", "comparison": "drop",
     "threshold": 30, "window_days": 7, "email": 0},
    {"name": "Sessions falling", "metric": "sessions", "comparison": "drop",
     "threshold": 30, "window_days": 7, "email": 0},
    {"name": "Error spike", "metric": "errors", "comparison": "spike",
     "threshold": 100, "window_days": 7, "email": 0},
)
MIN_CURRENT = {"pageviews": 10, "sessions": 10, "errors": 5}
MIN_PREVIOUS = {"pageviews": 10, "sessions": 10, "errors": 1}


def _dict(row):
    return {key: row[key] for key in row.keys()} if row else None


def period_key(now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    iso = now.isocalendar()
    return "%04d-W%02d" % (iso[0], iso[1])


def _change_pct(current, previous):
    current = float(current or 0)
    previous = float(previous or 0)
    if previous > 0:
        return round(((current - previous) / previous) * 100.0, 1)
    return 100.0 if current > 0 else 0.0


def list_rules(org_id, enabled_only=False):
    query = "SELECT * FROM analytics_alert_rules WHERE org_id = ?"
    params = [org_id]
    if enabled_only:
        query += " AND enabled = 1"
    query += " ORDER BY id ASC"
    with analytics.get_connection() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_dict(row) for row in rows]


def alerts_enabled(org_id):
    return settings.get_bool("ANALYTICS_ALERTS_ENABLED", True, org_id=org_id)


def ensure_default_rules(org_id):
    """Create the three in-app defaults once per client. Email stays off."""
    if not alerts_enabled(org_id):
        return []
    if list_rules(org_id):
        return list_rules(org_id)
    with analytics.get_connection() as conn:
        for item in DEFAULTS:
            conn.execute(
                "INSERT INTO analytics_alert_rules "
                "(org_id, name, metric, comparison, threshold, window_days, enabled, "
                "email) VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
                (org_id, item["name"], item["metric"], item["comparison"],
                 float(item["threshold"]), int(item["window_days"]),
                 int(item["email"])),
            )
    return list_rules(org_id)


def save_rule(org_id, data, rule_id=None):
    data = data or {}
    metric = str(data.get("metric") or "pageviews").strip().lower()
    if metric not in METRICS:
        raise ValueError("metric must be one of: %s" % ", ".join(METRICS))
    comparison = str(data.get("comparison") or "drop").strip().lower()
    if comparison not in ("drop", "spike"):
        raise ValueError("comparison must be 'drop' or 'spike'")
    try:
        threshold = float(data.get("threshold") or 30)
    except (TypeError, ValueError):
        raise ValueError("threshold must be a number")
    if threshold < 1 or threshold > 1000:
        raise ValueError("threshold must be between 1 and 1000 percent")
    try:
        window = int(data.get("window_days") or 7)
    except (TypeError, ValueError):
        raise ValueError("window_days must be a number")
    if window < 1 or window > 90:
        raise ValueError("window_days must be between 1 and 90")
    name = str(data.get("name") or "").strip()[:120]
    if not name:
        name = "%s %s" % (METRIC_LABELS[metric], comparison)
    enabled = 1 if data.get("enabled", True) else 0
    email = 1 if data.get("email", False) else 0
    with analytics.get_connection() as conn:
        if rule_id:
            row = conn.execute(
                "SELECT id FROM analytics_alert_rules WHERE id = ? AND org_id = ?",
                (rule_id, org_id)).fetchone()
            if not row:
                return None
            conn.execute(
                "UPDATE analytics_alert_rules SET name = ?, metric = ?, comparison = ?, "
                "threshold = ?, window_days = ?, enabled = ?, email = ?, updated_at = ? "
                "WHERE id = ? AND org_id = ?",
                (name, metric, comparison, threshold, window, enabled, email,
                 analytics.now_str(), rule_id, org_id))
            stored = rule_id
        else:
            count = conn.execute(
                "SELECT COUNT(*) FROM analytics_alert_rules WHERE org_id = ?",
                (org_id,)).fetchone()[0]
            if count >= 50:
                raise ValueError("A client can have at most 50 alert rules.")
            cur = conn.execute(
                "INSERT INTO analytics_alert_rules "
                "(org_id, name, metric, comparison, threshold, window_days, enabled, "
                "email) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (org_id, name, metric, comparison, threshold, window, enabled, email))
            stored = cur.lastrowid
    with analytics.get_connection() as conn:
        return _dict(conn.execute(
            "SELECT * FROM analytics_alert_rules WHERE id = ?", (stored,)).fetchone())


def delete_rule(org_id, rule_id):
    with analytics.get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM analytics_alert_rules WHERE id = ? AND org_id = ?",
            (rule_id, org_id)).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM analytics_alert_rules WHERE id = ? AND org_id = ?",
                     (rule_id, org_id))
    return True


def _metric_pair(brief, metric):
    current = brief.get("current", {}).get(metric, 0) or 0
    previous = brief.get("previous", {}).get(metric, 0) or 0
    return float(current), float(previous)


def _triggers(rule, current, previous):
    threshold = float(rule.get("threshold") or 0)
    comparison = str(rule.get("comparison") or "drop")
    metric = str(rule.get("metric") or "")
    pct = _change_pct(current, previous)
    if comparison == "drop":
        if previous < MIN_PREVIOUS.get(metric, 1):
            return False
        return current < previous and pct <= -threshold
    if comparison == "spike":
        if current < MIN_CURRENT.get(metric, 1):
            return False
        return current > previous and (previous == 0 or pct >= threshold)
    return False


def _friendly(value):
    value = float(value or 0)
    if abs(value - round(value)) < 0.01:
        return "{:,.0f}".format(value)
    return "{:,.1f}".format(value)


def _insert_alert(org_id, rule, current, previous, pct, period):
    with analytics.get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM analytics_alerts WHERE org_id = ? AND rule_id = ? "
            "AND period_key = ?", (org_id, rule["id"], period)).fetchone()
        if existing:
            return None
        label = METRIC_LABELS.get(rule.get("metric"), rule.get("metric") or "Metric")
        direction = "down" if pct < 0 else "up"
        title = "%s %s %s%%" % (label, direction, _friendly(abs(pct)))
        detail = ("Last %s days: %s, compared with %s in the previous %s days."
                  % (rule.get("window_days") or 7, _friendly(current),
                     _friendly(previous), rule.get("window_days") or 7))
        try:
            cur = conn.execute(
                "INSERT INTO analytics_alerts "
                "(org_id, rule_id, metric, title, detail, current_value, "
                "previous_value, change_pct, period_key, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')",
                (org_id, rule["id"], rule.get("metric") or "", title, detail,
                 current, previous, pct, period))
        except Exception:
            # Another worker inserted the same rule/week first; the unique key is the
            # guard, and a duplicate alert is not an error.
            return None
        alert_id = cur.lastrowid
    with analytics.get_connection() as conn:
        row = conn.execute("SELECT * FROM analytics_alerts WHERE id = ?",
                           (alert_id,)).fetchone() if alert_id else None
    return _dict(row)


def _rule_alert(org_id, rule, briefs, period, days):
    """Evaluate one rule. Kept separate so one bad rule cannot stop the rest."""
    window = max(1, min(90, int(rule.get("window_days") or days or 7)))
    if window not in briefs:
        briefs[window] = review.brief(org_id, days=window)
    brief = briefs[window]
    current, previous = _metric_pair(brief, rule.get("metric"))
    if not _triggers(rule, current, previous):
        return None
    pct = _change_pct(current, previous)
    return _insert_alert(org_id, rule, current, previous, pct, period)


def evaluate(org_id, days=7, notify=True, now=None):
    """Evaluate every enabled rule for one client. Safe to run repeatedly."""
    import actions

    if not alerts_enabled(org_id):
        return {"org_id": org_id, "alerts": 0, "items": [], "period_key": period_key(now),
                "email_sent": 0, "email_failed": [], "dry_run": actions.is_dry_run(),
                "disabled": True, "since": None, "days": days}
    ensure_default_rules(org_id)
    period = period_key(now)
    dry_run = actions.is_dry_run()
    created = []
    email_sent = 0
    email_failed = []
    briefs = {}
    errors = []
    for rule in list_rules(org_id, enabled_only=True):
        try:
            alert = _rule_alert(org_id, rule, briefs, period, days)
        except Exception as exc:
            errors.append("rule %s: %s"
                          % (rule.get("name") or rule.get("id"), str(exc)[:180]))
            continue
        if not alert:
            continue
        created.append(alert)
        if rule.get("email") and notify and not dry_run:
            try:
                delivery = notifications.notify_org(
                    org_id, "Analytics alert: %s" % alert["title"],
                    alert["detail"] + "\n\nOpen Analytics in your workspace.")
                if delivery.get("ok"):
                    email_sent += 1
                    with analytics.get_connection() as conn:
                        conn.execute(
                            "UPDATE analytics_alerts SET notified_at = ? WHERE id = ?",
                            (analytics.now_str(), alert["id"]))
                else:
                    email_failed.append(delivery.get("error") or "email not sent")
            except Exception as exc:
                email_failed.append(str(exc))
    first_brief = next(iter(briefs.values()), {}) if briefs else {}
    return {
        "org_id": org_id,
        "alerts": len(created),
        "items": created,
        "period_key": period,
        "email_sent": email_sent,
        "email_failed": email_failed[:3],
        "errors": errors,
        "dry_run": dry_run,
        "since": first_brief.get("since"),
        "days": days,
    }


def list_alerts(org_id, status=None, limit=50):
    query = ("SELECT a.*, r.name AS rule_name FROM analytics_alerts a "
             "LEFT JOIN analytics_alert_rules r ON r.id = a.rule_id "
             "WHERE a.org_id = ?")
    params = [org_id]
    if status:
        query += " AND a.status = ?"
        params.append(status)
    query += " ORDER BY a.id DESC LIMIT ?"
    params.append(max(1, int(limit)))
    with analytics.get_connection() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_dict(row) for row in rows]


def mark_alert(org_id, alert_id, status):
    status = str(status or "").strip().lower()
    if status not in ("open", "read", "resolved"):
        raise ValueError("status must be open, read or resolved")
    with analytics.get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM analytics_alerts WHERE id = ? AND org_id = ?",
            (alert_id, org_id)).fetchone()
        if not row:
            return None
        conn.execute("UPDATE analytics_alerts SET status = ? WHERE id = ? AND org_id = ?",
                     (status, alert_id, org_id))
        return _dict(conn.execute(
            "SELECT * FROM analytics_alerts WHERE id = ?", (alert_id,)).fetchone())


def prune_alerts(days=180, org_id=None):
    """Drop old read/resolved alerts. Open alerts stay until someone handles them."""
    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(days=max(1, int(days or 180)))
              ).strftime("%Y-%m-%d %H:%M:%S")
    query = ("DELETE FROM analytics_alerts WHERE status IN ('read', 'resolved') "
             "AND created_at < ?")
    params = [cutoff]
    if org_id is not None:
        query += " AND org_id = ?"
        params.append(org_id)
    with analytics.get_connection() as conn:
        cur = conn.execute(query, tuple(params))
        return cur.rowcount or 0


def insights(org_id, days=7):
    """Explainable changes, plus the top page/event worth noticing."""
    brief = review.brief(org_id, days=max(1, min(90, int(days or 7))))
    out = []

    def add(metric):
        current, previous = _metric_pair(brief, metric)
        if current == 0 and previous == 0:
            return
        pct = _change_pct(current, previous)
        if metric == "errors":
            if current < 5 and pct < 50:
                return
        elif abs(pct) < 5:
            return
        if pct == 0 and current == previous:
            return
        direction = "up" if pct > 0 else "down"
        if metric == "errors":
            severity = "critical" if pct >= 100 and current >= 10 else (
                "warning" if pct >= 50 else "info")
        elif pct <= -30:
            severity = "critical"
        elif pct <= -10:
            severity = "warning"
        else:
            severity = "info"
        out.append({
            "key": metric,
            "severity": severity,
            "title": "%s %s %s%%" % (METRIC_LABELS[metric], direction,
                                     _friendly(abs(pct))),
            "detail": ("Last %d days: %s, compared with %s in the previous %d days."
                       % (brief["days"], _friendly(current), _friendly(previous),
                          brief["days"])),
            "metric": metric,
            "current": current,
            "previous": previous,
            "change_pct": pct,
        })

    add("pageviews")
    add("sessions")
    add("errors")
    top_pages = brief.get("current", {}).get("top_pages") or []
    if top_pages:
        page = top_pages[0]
        out.append({
            "key": "top_page",
            "severity": "info",
            "title": "Top page: %s" % page.get("path", "/"),
            "detail": "%s views in the last %d days." % (
                _friendly(page.get("views")), brief["days"]),
            "metric": "pageviews",
        })

    # GA4 and BigQuery both expose an eventName dimension in their event facts. Show the
    # busiest one so the client knows which event is actually being measured.
    try:
        facts = analytics.facts(org_id, brief["since"], limit=5000)
        event_counts = {}
        for fact in facts:
            if fact.get("metric") != "eventCount" or fact.get("report") not in (
                    "events", "bq_events"):
                continue
            dims = fact.get("dimensions") or {}
            name = dims.get("eventName") or dims.get("event_name")
            if name:
                event_counts[name] = event_counts.get(name, 0) + float(
                    fact.get("value") or 0)
        if event_counts:
            name, count = max(event_counts.items(), key=lambda pair: pair[1])
            out.append({
                "key": "top_event",
                "severity": "info",
                "title": "Top event: %s" % name,
                "detail": "%s events in the last %d days." % (
                    _friendly(count), brief["days"]),
                "metric": "events",
            })
    except Exception:
        pass

    order = {"critical": 0, "warning": 1, "info": 2}
    out.sort(key=lambda item: order.get(item.get("severity"), 3))
    return {
        "since": brief["since"],
        "days": brief["days"],
        "current": brief["current"],
        "previous": brief["previous"],
        "change": brief["change"],
        "insights": out[:8],
    }


import jobs as _jobs


@_jobs.task("analytics_alerts")
async def analytics_alerts_task(job, emit):
    """Scheduler task: evaluate this client's alert rules."""
    org_id = job.get("org_id")
    if org_id is None:
        emit("alerts", "error", "This alert run is not attached to a client.")
        return "failed: no client"
    days = int(job.get("days") or 7)
    try:
        result = evaluate(org_id, days=days, notify=True)
    except Exception as exc:
        emit("alerts", "error", "Analytics alert run failed: %s" % exc)
        return "failed: %s" % exc
    try:
        analytics.record_action(
            org_id, "alerts_evaluate", target="analytics", actor="scheduler",
            result="%s alert(s)" % result.get("alerts"))
    except Exception:
        pass
    emit("alerts", "system", "Evaluated analytics alerts: %d new alert(s)."
         % result.get("alerts", 0))
    return "created %d alert(s)" % result.get("alerts", 0)


