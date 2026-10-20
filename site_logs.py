"""Site traffic from Caddy's own access log.

The cheapest honest source we have: the web server in front of every hosted site already
records each request, so no third-party account, no consent banner and no API quota.
It is also the only source that sees 404s, which is usually the first thing worth fixing
on a client's site.

Caddy writes JSON by default. Only aggregates are stored - counts per day per host,
error totals and top pages. Remote IPs are never written anywhere: nothing here can
accumulate personal data.
"""

import json
import os
import re
from datetime import datetime, timezone

import analytics
import sites as sites_service

DEFAULT_LOG = "/var/log/caddy/access.log"

# Caddy's JSON, or Common Log Format if the operator configured `format console`.
_CLF = re.compile(
    r'^(?P<host>\S+) \S+ \S+ \[[^\]]+\] "(?P<method>[A-Z]+) (?P<uri>\S+)[^"]*" (?P<status>\d{3})'
)


def log_path():
    return (os.environ.get("CADDY_ACCESS_LOG") or DEFAULT_LOG).strip()


def _caddy_source():
    """One platform-level source for the whole deployment, created on first use."""
    for source in analytics.sources(org_id=None):
        if source["kind"] == "caddy":
            return source
    return analytics.save_source(
        {"kind": "caddy", "label": "Site logs (this server)"}, org_id=None
    )


def parse_line(line):
    """``(host, path, status, day)`` for one log line, or None if it is not a request."""
    line = (line or "").strip()
    if not line:
        return None
    try:
        entry = json.loads(line)
    except ValueError:
        match = _CLF.match(line)
        if not match:
            return None
        return (
            match.group("host").split(":")[0].lower(),
            match.group("uri").split("?")[0],
            int(match.group("status")),
            "",
        )

    request = entry.get("request") or {}
    host = str(request.get("host") or "").split(":")[0].lower()
    if not host:
        return None
    status = entry.get("status")
    try:
        status = int(status)
    except (TypeError, ValueError):
        status = 0
    stamp = entry.get("ts")
    try:
        day = datetime.fromtimestamp(float(stamp), timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        day = ""
    path = str(request.get("uri") or "/").split("?")[0]
    return host, path, status, day


def sync(path=None, since_days=1):
    """Read the log and store per-site aggregates. Returns a summary dict.

    Re-running over the same file is safe: ``metrics_daily`` has a unique point key, so
    the same window cannot double-count.
    """
    source = _caddy_source()
    run = analytics.start_sync(source["id"])
    target = path or log_path()
    try:
        if not os.path.exists(target):
            raise RuntimeError(
                "No access log at %s. Add `log { output file %s }` to the Caddyfile (and "
                "mount the directory into this container), or set CADDY_ACCESS_LOG." % (target, target)
            )
        per_org = {}
        ignored = 0
        with open(target, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                parsed = parse_line(line)
                if not parsed:
                    continue
                host, uri, status, day = parsed
                if not day:
                    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                site = sites_service.get_site_by_domain(host)
                if not site or site.get("org_id") is None:
                    ignored += 1
                    continue
                org = site["org_id"]
                bucket = per_org.setdefault(
                    org, {"pageviews": {}, "errors": {}, "pages": {}}
                )
                bucket["pageviews"][day] = bucket["pageviews"].get(day, 0) + 1
                if status >= 400:
                    bucket["errors"][day] = bucket["errors"].get(day, 0) + 1
                key = (day, uri)
                bucket["pages"][key] = bucket["pages"].get(key, 0) + 1
    except Exception as exc:
        analytics.finish_sync(run, "error", rows=0, error=str(exc))
        return {"ok": False, "error": str(exc)}

    rows = 0
    for org, bucket in per_org.items():
        points = [("pageviews", "", day, value) for day, value in bucket["pageviews"].items()]
        points += [("errors", "", day, value) for day, value in bucket["errors"].items()]
        points += [
            ("page", uri, day, value) for (day, uri), value in bucket["pages"].items()
        ]
        rows += analytics.record_metrics(org, "caddy", points)

    analytics.finish_sync(run, "ok", rows=rows)
    return {
        "ok": True,
        "rows": rows,
        "orgs": len(per_org),
        "ignored": ignored,
        "path": target,
    }


def recent(org_id, days=7):
    """The brief a weekly review reads: totals, errors and top pages, no PII."""
    from datetime import timedelta

    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = analytics.metrics(org_id, since, source="caddy")
    pageviews = sum(r["value"] for r in rows if r["metric"] == "pageviews")
    errors = sum(r["value"] for r in rows if r["metric"] == "errors")
    pages = {}
    for row in rows:
        if row["metric"] == "page":
            pages[row["dimension"]] = pages.get(row["dimension"], 0) + row["value"]
    top = sorted(pages.items(), key=lambda pair: pair[1], reverse=True)[:10]
    return {
        "since": since,
        "pageviews": pageviews,
        "errors": errors,
        "top_pages": [{"path": path, "views": int(views)} for path, views in top],
    }
