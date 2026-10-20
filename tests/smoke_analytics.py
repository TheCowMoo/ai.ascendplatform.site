"""Data sources: the bindings every analytics adapter reads through.

Run: .\\venv\\Scripts\\python.exe tests\\smoke_analytics.py

GHL is the operator's sub-account; GA4 and Meta belong to the client when they have
them. So the interesting properties are scoping (one client's accounts never leak into
another's), idempotent metrics (a 7-day lookback re-read daily must not double-count)
and honest sync records (a failed pull is visible, not silent).
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analytics  # noqa: E402
import app as appmod  # noqa: E402
import db  # noqa: E402
import orgs as orgs_service  # noqa: E402

TOKEN = uuid.uuid4().hex[:6]


def _org(name):
    return orgs_service.create_org({"name": "%s %s" % (name, TOKEN)})


def test_sources_are_scoped_to_their_org():
    alpha = _org("Analytics Alpha")
    beta = _org("Analytics Beta")
    source = analytics.save_source(
        {"kind": "ghl", "external_id": "loc-123", "label": "Ascend sub-account"},
        org_id=alpha["id"],
    )
    assert source["org_id"] == alpha["id"], source
    assert source["kind"] == "ghl" and source["enabled"] is True
    assert [s["id"] for s in analytics.sources(alpha["id"])] == [source["id"]]
    assert analytics.sources(beta["id"]) == []
    print("source #%s scoped to org %s only" % (source["id"], alpha["id"]))
    return alpha, source


def test_a_source_must_be_addressable():
    for bad in ({"kind": "nope"}, {"kind": "ga4"}, {"kind": "meta"}):
        try:
            analytics.save_source(bad)
        except analytics.SourceError as exc:
            print("  %-22s -> %s" % (bad["kind"], exc))
        else:
            raise AssertionError("accepted an unaddressable source: %r" % bad)
    # Site logs need no external id: the files are ours.
    assert analytics.save_source({"kind": "caddy"}, org_id=None)["kind"] == "caddy"


def test_metrics_are_idempotent_over_an_overlapping_window():
    org = _org("Analytics Metrics")
    points = [
        ("sessions", "", "2026-10-19", 12),
        ("sessions", "", "2026-10-20", 18),
        ("top_page", "/pricing", "2026-10-20", 7),
    ]
    analytics.record_metrics(org["id"], "caddy", points)
    # The same window again, as every daily sync would: no double counting.
    analytics.record_metrics(org["id"], "caddy", points)
    rows = analytics.metrics(org["id"], "2026-10-19")
    assert len(rows) == 3, rows
    sessions = [r for r in rows if r["metric"] == "sessions"]
    assert [r["value"] for r in sessions] == [12, 18], sessions
    print("re-running the same window kept %d rows" % len(rows))


def test_a_failed_sync_is_visible():
    org = _org("Analytics Sync")
    source = analytics.save_source(
        {"kind": "ga4", "external_id": "properties/123", "credential_ref": "/run/secrets/ga4.json"},
        org_id=org["id"],
    )
    run = analytics.start_sync(source["id"])
    analytics.finish_sync(run, "ok", rows=42)
    assert analytics.get_source(source["id"])["last_sync_at"], "a successful sync must stamp the source"
    assert analytics.latest_sync(source["id"])["status"] == "ok"

    failed = analytics.start_sync(source["id"])
    analytics.finish_sync(failed, "error", rows=0, error="403 PERMISSION_DENIED")
    after = analytics.get_source(source["id"])
    assert "PERMISSION_DENIED" in after["last_error"], after
    print("failed sync recorded: %s" % after["last_error"])


def test_the_api_is_client_scoped():
    db.init_db()
    client = appmod.app.test_client()
    login = client.post("/api/auth/login",
                        json={"email": "admin@example.com", "password": "admin123"})
    assert login.status_code == 200, login.get_data(as_text=True)
    org = _org("Analytics API")
    resp = client.post("/api/data-sources",
                       json={"kind": "ga4", "external_id": "properties/999",
                             "org_id": org["id"], "label": "Client GA4"})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    created = resp.get_json()["source"]
    assert created["org_id"] == org["id"], created

    listed = client.get("/api/data-sources?org_id=%d" % org["id"]).get_json()
    assert created["id"] in [s["id"] for s in listed["sources"]], listed
    assert {"kind": "meta", "label": "Meta ads"} in listed["kinds"], listed["kinds"]

    assert client.post("/api/data-sources", json={"kind": "nope"}).status_code == 400
    assert client.delete("/api/data-sources/%d" % created["id"]).status_code == 200
    print("API created, listed and deleted a source for org %s" % org["id"])


def main():
    test_sources_are_scoped_to_their_org()
    test_a_source_must_be_addressable()
    test_metrics_are_idempotent_over_an_overlapping_window()
    test_a_failed_sync_is_visible()
    test_the_api_is_client_scoped()
    print("\nANALYTICS OK")


if __name__ == "__main__":
    main()
