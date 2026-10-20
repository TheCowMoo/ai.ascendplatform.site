"""The weekly brief and the prompt built from it.

Run: .\\venv\\Scripts\\python.exe tests\\smoke_review.py

The brief has to compare two windows (a number with no baseline invites invented
advice), and the prompt must demand a metric per recommendation.
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analytics  # noqa: E402
import db  # noqa: E402
import orgs as orgs_service  # noqa: E402
import review  # noqa: E402

TOKEN = uuid.uuid4().hex[:6]


def _day(offset):
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(days=offset)).strftime("%Y-%m-%d")


def test_the_brief_compares_two_windows():
    db.init_db()
    org = orgs_service.create_org({"name": "Review %s" % TOKEN})
    analytics.record_metrics(org["id"], "caddy", [
        ("pageviews", "", _day(2), 10),
        ("errors", "", _day(2), 1),
        ("page", "/pricing", _day(2), 6),
        ("pageviews", "", _day(9), 4),
        ("page", "/", _day(9), 2),
    ])
    data = review.brief(org["id"], days=7)
    assert data["current"]["pageviews"] == 10, data
    assert data["previous"]["pageviews"] == 4, data
    assert data["change"]["pageviews"] == 6, data
    assert data["current"]["top_pages"][0] == {"path": "/pricing", "views": 6}, data
    assert "caddy" in data["sources"], data
    print("brief ->", data["current"], "vs", data["previous"])


def test_the_prompt_demands_evidence():
    data = {
        "days": 7,
        "change": {"pageviews": -3, "sessions": 0, "errors": 2},
        "current": {"pageviews": 11, "sessions": 5, "errors": 3,
                    "top_pages": [{"path": "/", "views": 8}]},
    }
    text = review.prompt_for(data, "acme.test")
    for expected in ("acme.test", "11 pageviews (-3", "3 errors (+2)", "/ (8)"):
        assert expected in text, (expected, text)
    assert "name the metric" in text, text
    print("prompt ->", text.splitlines()[1])


def main():
    test_the_brief_compares_two_windows()
    test_the_prompt_demands_evidence()
    print("\nREVIEW OK")


if __name__ == "__main__":
    main()
