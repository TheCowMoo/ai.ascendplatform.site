"""Google Analytics 4: one property per client, read as aggregates.

The client owns the property. They can authorize it with their own Google account
(``analytics_oauth.py``), or an existing source can keep using the platform service
account and a mounted key file. Two calls per sync - obtain an access token, then
``runReport`` - and the HTTP layer is replaceable so all of it is testable offline.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

import settings

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
    import analytics_oauth

    if analytics_oauth.is_oauth_source(source):
        connection = analytics_oauth.get_connection(source.get("org_id"))
        marker_id = analytics_oauth.connection_id_from_source(source)
        if not connection or (marker_id and connection.get("id") != marker_id):
            raise RuntimeError(
                "Google Analytics authorization is missing. Reconnect Google Analytics."
            )
        try:
            token = analytics_oauth.access_token_for_org(source["org_id"])
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
    report = run_report(source["external_id"], token)
    rows = analytics.record_metrics(source["org_id"], "ga4", to_points(report))
    return {"ok": True, "rows": rows}
