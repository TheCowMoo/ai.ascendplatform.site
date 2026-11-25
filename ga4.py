"""Google Analytics 4: the read-only Data API adapter.

The client owns the property. They can authorize it with their own Google account
(``analytics_oauth.py``), or an existing source can keep using the platform service
account and a mounted key file. Reports are aggregates; raw event-level data belongs to
the BigQuery phase, not this module.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

import settings

TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/analytics.readonly"

# The curated report catalogue. "date" is always present so every fact is a daily point.
REPORTS = {
    "traffic": {
        "dimensions": ["date", "sessionDefaultChannelGroup", "sessionSource",
                       "sessionMedium"],
        "metrics": ["sessions", "engagedSessions", "engagementRate", "keyEvents"],
        "limit": 10000,
    },
    "pages": {
        "dimensions": ["date", "pagePath", "pageTitle"],
        "metrics": ["screenPageViews", "sessions", "engagedSessions", "keyEvents"],
        "limit": 10000,
    },
    "events": {
        "dimensions": ["date", "eventName"],
        "metrics": ["eventCount", "keyEvents", "totalUsers"],
        "limit": 10000,
    },
    "audience": {
        "dimensions": ["date", "country", "deviceCategory"],
        "metrics": ["activeUsers", "sessions", "engagedSessions", "keyEvents"],
        "limit": 10000,
    },
}
SYNC_REPORTS = tuple(REPORTS)



def _transport(url, payload=None, headers=None, timeout=20):
    """POST/GET returning ``(status, body_text)``. Tests replace this."""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def access_token(key_path):
    """Exchange a service-account key for an access token."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    import base64

    with open(key_path, "r", encoding="utf-8") as handle:
        key = json.load(handle)
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "iss": key["client_email"],
        "scope": SCOPE,
        "aud": TOKEN_URL,
        "iat": now,
        "exp": now + 3600,
    }

    def encode(part):
        raw = json.dumps(part, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    signing_input = "%s.%s" % (encode(header), encode(claims))
    private_key = serialization.load_pem_private_key(
        key["private_key"].encode("utf-8"), password=None
    )
    signature = private_key.sign(
        signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256()
    )
    assertion = "%s.%s" % (
        signing_input,
        base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii"),
    )
    status, body = _transport(TOKEN_URL, {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                          "assertion": assertion})
    if status >= 400:
        raise RuntimeError("GA4 token exchange failed (%s): %s" % (status, body[:300]))
    return json.loads(body)["access_token"]


def run_report(property_id, token, days=7):
    """Sessions and pageviews per day, plus the top pages. Aggregates only."""
    url = "https://analyticsdata.googleapis.com/v1beta/%s:runReport" % property_id
    payload = {
        "dateRanges": [{"startDate": "%ddaysAgo" % days, "endDate": "yesterday"}],
        "dimensions": [{"name": "date"}, {"name": "pagePath"}],
        "metrics": [{"name": "sessions"}, {"name": "screenPageViews"}],
        "limit": 1000,
    }
    status, body = _transport(url, payload, {"Authorization": "Bearer %s" % token,
                                             "Content-Type": "application/json"})
    if status >= 400:
        raise RuntimeError("GA4 runReport failed (%s): %s" % (status, body[:300]))
    return json.loads(body)


def to_points(report):
    """A GA4 report as ``[(metric, dimension, day, value)]`` for ``record_metrics``.

    Pages are kept as dimensions; dates become ``YYYY-MM-DD`` because GA4 returns
    ``YYYYMMDD``. Nothing user-level is retained - there is nothing to retain.
    """
    points = []
    for row in report.get("rows") or []:
        dims = [d.get("value", "") for d in row.get("dimensionValues") or []]
        values = [m.get("value", "0") for m in row.get("metricValues") or []]
        if len(dims) < 2 or len(values) < 2:
            continue
        raw_day, path = dims[0], dims[1]
        day = "%s-%s-%s" % (raw_day[:4], raw_day[4:6], raw_day[6:8]) if len(raw_day) == 8 else raw_day
        points.append(("sessions", "", day, float(values[0] or 0)))
        points.append(("pageviews", "", day, float(values[1] or 0)))
        points.append(("page", path, day, float(values[1] or 0)))
    return points


def _day(value):
    raw = str(value or "")
    if len(raw) == 8 and raw.isdigit():
        return "%s-%s-%s" % (raw[:4], raw[4:6], raw[6:8])
    return raw


def _headers(report, key, fallback):
    names = [item.get("name") for item in report.get(key) or []]
    return [name for name in names if name] or list(fallback)


def run_named_report(property_id, token, report_name, days=7):
    """One curated report from :data:`REPORTS`, as the GA4 JSON response."""
    spec = REPORTS.get(report_name)
    if not spec:
        raise RuntimeError("Unknown GA4 report %r." % report_name)
    url = ("https://analyticsdata.googleapis.com/v1beta/%s:runReport"
           % property_id)
    payload = {
        "dateRanges": [{"startDate": "%ddaysAgo" % max(1, int(days or 7)),
                        "endDate": "yesterday"}],
        "dimensions": [{"name": name} for name in spec["dimensions"]],
        "metrics": [{"name": name} for name in spec["metrics"]],
        "limit": spec.get("limit") or 10000,
    }
    status, body = _transport(url, payload, {
        "Authorization": "Bearer %s" % token,
        "Content-Type": "application/json",
    })
    if status >= 400:
        raise RuntimeError("GA4 %s report failed (%s): %s"
                           % (report_name, status, body[:300]))
    return json.loads(body)


def to_facts(report_name, report):
    """``[(report, metric, dimensions, day, value)]`` for ``analytics.record_facts``."""
    dim_names = _headers(report, "dimensionHeaders", REPORTS[report_name]["dimensions"])
    metric_names = _headers(report, "metricHeaders", REPORTS[report_name]["metrics"])
    facts = []
    for row in report.get("rows") or []:
        dims = {}
        for index, name in enumerate(dim_names):
            values = row.get("dimensionValues") or []
            dims[name] = values[index].get("value", "") if index < len(values) else ""
        day = _day(dims.get("date", ""))
        for index, metric in enumerate(metric_names):
            values = row.get("metricValues") or []
            raw = values[index].get("value", "0") if index < len(values) else "0"
            try:
                number = float(raw or 0)
            except (TypeError, ValueError):
                number = 0.0
            facts.append((report_name, metric, dims, day, number))
    return facts


def to_legacy_points(report):
    """Sessions/pageviews/top-page points from the pages report.

    ``metrics_daily`` stays the simple series the weekly review already reads, so a
    richer fact table does not break the existing brief.
    """
    dim_names = _headers(report, "dimensionHeaders", REPORTS["pages"]["dimensions"])
    metric_names = _headers(report, "metricHeaders", REPORTS["pages"]["metrics"])
    points = []
    for row in report.get("rows") or []:
        dims, values = {}, {}
        for index, name in enumerate(dim_names):
            source = row.get("dimensionValues") or []
            dims[name] = source[index].get("value", "") if index < len(source) else ""
        for index, name in enumerate(metric_names):
            source = row.get("metricValues") or []
            raw = source[index].get("value", "0") if index < len(source) else "0"
            try:
                values[name] = float(raw or 0)
            except (TypeError, ValueError):
                values[name] = 0.0
        day = _day(dims.get("date", ""))
        path = dims.get("pagePath", "")
        views = values.get("screenPageViews", 0.0)
        sessions = values.get("sessions", 0.0)
        points.append(("sessions", "", day, sessions))
        points.append(("pageviews", "", day, views))
        points.append(("page", path, day, views))
    return points


def sync(source):
    """Pull every curated report for one GA4 source. Raises when nothing worked."""
    import analytics
    import analytics_oauth

    if analytics_oauth.is_oauth_source(source):
        try:
            token = analytics_oauth.access_token_for_source(source)
        except analytics_oauth.OAuthError as exc:
            raise RuntimeError(str(exc))
    else:
        # Existing service-account connections still work. A client only needs to
        # share the property with the platform's service account.
        key_path = (source.get("credential_ref") or "").strip()
        if not key_path:
            key_path = settings.get("GA4_SERVICE_ACCOUNT_KEY_PATH")
        if not key_path:
            raise RuntimeError(
                "Google Analytics is not configured: add the service-account key path "
                "in Platform settings, then try again."
            )
        token = access_token(key_path)

    facts_written = 0
    legacy_written = 0
    used, errors = [], []
    for report_name in SYNC_REPORTS:
        try:
            report = run_named_report(source["external_id"], token, report_name)
        except Exception as exc:
            errors.append("%s: %s" % (report_name, str(exc)[:200]))
            continue
        facts_written += analytics.record_facts(
            source["org_id"], source["id"], to_facts(report_name, report))
        if report_name == "pages":
            legacy_written += analytics.record_metrics(
                source["org_id"], "ga4", to_legacy_points(report))
        used.append(report_name)
    if not used:
        raise RuntimeError("; ".join(errors) or "Google Analytics returned no reports.")
    return {
        "ok": True,
        "rows": facts_written,
        "legacy_rows": legacy_written,
        "reports": used,
        "errors": errors,
    }
