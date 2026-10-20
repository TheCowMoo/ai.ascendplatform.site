"""Caddy access logs become per-site metrics, with no third-party account.

Run: .\\venv\\Scripts\\python.exe tests\\smoke_site_logs.py

The properties that matter: a request is attributed to the org that owns the domain,
an unknown host is ignored rather than crashing, re-running over the same file does not
double-count, and no IP address is ever stored.
"""

import json
import os
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analytics  # noqa: E402
import db  # noqa: E402
import orgs as orgs_service  # noqa: E402
import site_logs  # noqa: E402
import sites as sites_service  # noqa: E402

TOKEN = uuid.uuid4().hex[:6]


def _org_with_site(domain):
    org = orgs_service.create_org({"name": "Logs %s %s" % (domain, TOKEN)})
    sites_service.create_site({"domain": domain}, org_id=org["id"])
    return org


def _write_log(lines):
    handle = tempfile.NamedTemporaryFile("w", suffix=".log", delete=False, encoding="utf-8")
    for line in lines:
        handle.write(line + "\n")
    handle.close()
    return handle.name


def _line(host, uri, status=200, ts=1_758_000_000.0):
    return json.dumps({
        "level": "info",
        "ts": ts,
        "logger": "http.log.access",
        "msg": "handled request",
        "request": {"remote_ip": "203.0.113.9", "host": host, "uri": uri, "method": "GET"},
        "status": status,
        "size": 512,
    })


def test_requests_are_attributed_to_the_owning_org():
    db.init_db()
    known = "%s-known.test" % TOKEN
    org = _org_with_site(known)
    path = _write_log([
        _line(known, "/"),
        _line(known, "/pricing"),
        _line(known, "/pricing"),
        _line(known, "/missing", status=404),
        _line("someone-elses.test", "/"),          # not ours: ignored
        "garbage that is not a log line",
    ])
    try:
        summary = site_logs.sync(path=path)
    finally:
        os.unlink(path)
    print("sync ->", summary)
    assert summary["ok"] is True, summary
    assert summary["ignored"] == 1, summary

    brief = site_logs.recent(org["id"], days=3650)
    assert brief["pageviews"] == 4, brief
    assert brief["errors"] == 1, brief
    assert brief["top_pages"][0] == {"path": "/pricing", "views": 2}, brief["top_pages"]


def test_rerunning_the_same_file_does_not_double_count():
    known = "%s-twice.test" % TOKEN
    org = _org_with_site(known)
    path = _write_log([_line(known, "/"), _line(known, "/")])
    try:
        site_logs.sync(path=path)
        site_logs.sync(path=path)
    finally:
        os.unlink(path)
    brief = site_logs.recent(org["id"], days=3650)
    assert brief["pageviews"] == 2, brief
    print("re-run kept pageviews at", brief["pageviews"])


def test_a_missing_log_explains_what_to_configure():
    result = site_logs.sync(path="/nonexistent/caddy/access.log")
    assert result["ok"] is False, result
    assert "Caddyfile" in result["error"], result
    print("missing log ->", result["error"][:90])


def test_no_ip_address_or_pii_is_stored():
    known = "%s-pii.test" % TOKEN
    org = _org_with_site(known)
    path = _write_log([_line(known, "/")])
    try:
        site_logs.sync(path=path)
    finally:
        os.unlink(path)
    rows = analytics.metrics(org["id"], "2000-01-01", source="caddy")
    blob = json.dumps(rows)
    assert "203.0.113.9" not in blob, "an IP address reached the metrics table"
    assert set(r["metric"] for r in rows) <= {"pageviews", "errors", "page"}, rows
    print("metrics carry no IP: %s" % sorted(set(r["metric"] for r in rows)))


def test_the_sync_route_is_operator_only():
    import app as appmod

    anon = appmod.app.test_client()
    assert anon.post("/api/data-sources/sync").status_code in (302, 401, 403)
    print("sync route is not public")


def main():
    test_requests_are_attributed_to_the_owning_org()
    test_rerunning_the_same_file_does_not_double_count()
    test_a_missing_log_explains_what_to_configure()
    test_no_ip_address_or_pii_is_stored()
    test_the_sync_route_is_operator_only()
    print("\nSITE LOGS OK")


if __name__ == "__main__":
    main()
