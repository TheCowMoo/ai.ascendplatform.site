"""Google Analytics 4: one property per client, read as aggregates.

The client owns the property, so there is no OAuth dance: they add our service account
to the GA4 property, and we read it with a key file mounted at a path the data source
points to. Two calls per sync - exchange the signed JWT for an access token, then
``runReport`` - and the HTTP layer is replaceable so all of it is testable offline.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/analytics.readonly"


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


def sync(source):
    """Pull one GA4 source and store it. Returns a summary; raises on failure."""
    import analytics

    token = access_token(source["credential_ref"])
    report = run_report(source["external_id"], token)
    rows = analytics.record_metrics(source["org_id"], "ga4", to_points(report))
    return {"ok": True, "rows": rows}
