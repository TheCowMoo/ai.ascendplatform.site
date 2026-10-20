"""GA4 reports become the same metrics rows as everything else.

Run: .\\venv\\Scripts\\python.exe tests\\smoke_ga4.py

No network: the report-to-points conversion is pure, which is the part that would
silently corrupt the weekly brief if it were wrong (GA4 dates, page dimensions).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ga4  # noqa: E402

REPORT = {
    "rows": [
        {"dimensionValues": [{"value": "20261019"}, {"value": "/"}],
         "metricValues": [{"value": "40"}, {"value": "63"}]},
        {"dimensionValues": [{"value": "20261020"}, {"value": "/pricing"}],
         "metricValues": [{"value": "25"}, {"value": "31"}]},
    ]
}


def test_dates_and_pages_become_metric_points():
    points = ga4.to_points(REPORT)
    days = {p[2] for p in points}
    assert days == {"2026-10-19", "2026-10-20"}, days
    sessions = [p for p in points if p[0] == "sessions"]
    assert sessions[0][3] == 40.0 and sessions[1][3] == 25.0, sessions
    pages = {p[1]: p[3] for p in points if p[0] == "page"}
    assert pages == {"/": 63.0, "/pricing": 31.0}, pages
    print("GA4 ->", len(points), "points over", sorted(days))


def test_a_malformed_row_is_skipped_not_fatal():
    broken = {"rows": [{"dimensionValues": [{"value": "20261019"}], "metricValues": []}]}
    assert ga4.to_points(broken) == []
    assert ga4.to_points({}) == []
    print("malformed rows skipped")


def main():
    test_dates_and_pages_become_metric_points()
    test_a_malformed_row_is_skipped_not_fatal()
    print("\nGA4 OK")


if __name__ == "__main__":
    main()
