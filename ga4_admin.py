"""Read-only Google Analytics Admin API discovery.

Phase 1 only needs to list the properties a Google account can reach and fetch the
metadata for the one the client picks. Write operations belong to the later approval
phase and are deliberately absent from this module.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://analyticsadmin.googleapis.com/v1beta"


class GoogleApiError(Exception):
    """A Google API problem phrased for the person connecting the account."""


def _transport(url, headers=None, timeout=20):
    """GET returning ``(status, body_text)``. Tests replace this."""
    request = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def _get(token, url):
    status, body = _transport(url, {
        "Authorization": "Bearer %s" % token,
        "Accept": "application/json",
    })
    if status in (401, 403):
        raise GoogleApiError(
            "Google does not grant Analytics access to this connection. Reconnect "
            "Google and approve the Analytics permission."
        )
    if status == 404:
        raise GoogleApiError("That Google Analytics property was not found.")
    if status >= 400:
        raise GoogleApiError("Google Analytics returned %s: %s"
                             % (status, str(body)[:300]))
    try:
        return json.loads(body)
    except ValueError:
        raise GoogleApiError("Google Analytics returned an unreadable response.")


def list_account_summaries(token):
    """Every property the account can see, flattened and in plain fields."""
    properties = []
    page_token = ""
    for _ in range(10):
        query = {"pageSize": "200"}
        if page_token:
            query["pageToken"] = page_token
        url = BASE + "/accountSummaries?" + urllib.parse.urlencode(query)
        data = _get(token, url)
        for account in data.get("accountSummaries") or []:
            account_name = account.get("displayName") or ""
            account_id = account.get("account") or ""
            for item in account.get("propertySummaries") or []:
                properties.append({
                    "property": item.get("property") or "",
                    "property_id": str(item.get("property") or "").split("/")[-1],
                    "display_name": item.get("displayName") or "",
                    "account": account_id,
                    "account_name": account_name,
                    "property_type": item.get("propertyType") or "",
                    "parent": item.get("parent") or "",
                })
        page_token = data.get("nextPageToken") or ""
        if not page_token:
            break
    return properties


def get_property(token, property_id):
    """Metadata for one property: time zone, currency and canonical name."""
    raw = str(property_id or "").strip()
    if raw.startswith("properties/"):
        raw = raw.split("/", 1)[1]
    if not raw:
        raise GoogleApiError("Choose a Google Analytics property first.")
    data = _get(token, BASE + "/properties/" + urllib.parse.quote(raw))
    return {
        "property": data.get("name") or ("properties/" + raw),
        "property_id": str(data.get("name") or ("properties/" + raw)).split("/")[-1],
        "display_name": data.get("displayName") or "",
        "account": data.get("account") or "",
        "time_zone": data.get("timeZone") or "",
        "currency_code": data.get("currencyCode") or "",
        "property_type": data.get("propertyType") or "",
    }


def list_properties(token):
    """The picker's list. Metadata for a chosen property comes from get_property."""
    return list_account_summaries(token)
