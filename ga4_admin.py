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
BASE_ALPHA = "https://analyticsadmin.googleapis.com/v1alpha"


class GoogleApiError(Exception):
    """A Google API problem phrased for the person connecting the account."""


def _transport(url, headers=None, timeout=20, method="GET", payload=None):
    """Return ``(status, body_text)``. Tests replace this."""
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers = dict(headers or {})
        headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=data, headers=headers or {},
                                     method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def _request(token, url, method="GET", payload=None):
    headers = {
        "Authorization": "Bearer %s" % token,
        "Accept": "application/json",
    }
    if method == "GET" and payload is None:
        # Older tests and callers replace _transport with a GET-only fake.
        status, body = _transport(url, headers)
    else:
        status, body = _transport(url, headers, method=method, payload=payload)
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


def _get(token, url):
    return _request(token, url)


def _post(token, url, payload):
    return _request(token, url, method="POST", payload=payload)


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


def list_bigquery_links(token, property_id):
    """BigQuery export links on one GA4 property."""
    data = _get(token, BASE_ALPHA + "/" + _property_path(property_id)
                + "/bigQueryLinks?pageSize=200")
    links = []
    for item in data.get("bigqueryLinks") or []:
        links.append({
            "name": item.get("name") or "",
            "project": item.get("project") or "",
            "dataset_location": item.get("datasetLocation") or "",
            "daily_export_enabled": bool(item.get("dailyExportEnabled")),
            "streaming_export_enabled": bool(item.get("streamingExportEnabled")),
            "fresh_daily_export_enabled": bool(item.get("freshDailyExportEnabled")),
        })
    return links


def create_bigquery_link(token, property_id, project, dataset_location="US",
                         daily_export=True, streaming_export=False,
                         fresh_daily_export=False):
    """Link a GA4 property to a Google Cloud project for the BigQuery export."""
    name = str(project or "").strip()
    if not name:
        raise GoogleApiError("Enter the Google Cloud project ID that will own the export.")
    if not name.startswith("projects/"):
        name = "projects/" + name
    location = str(dataset_location or "US").strip()
    if not location:
        raise GoogleApiError("Choose the BigQuery dataset location.")
    data = _post(token, BASE_ALPHA + "/" + _property_path(property_id)
                 + "/bigQueryLinks", {
                     "project": name,
                     "datasetLocation": location,
                     "dailyExportEnabled": bool(daily_export),
                     "streamingExportEnabled": bool(streaming_export),
                     "freshDailyExportEnabled": bool(fresh_daily_export),
                 })
    return {
        "name": data.get("name") or "",
        "project": data.get("project") or name,
        "dataset_location": data.get("datasetLocation") or location,
        "daily_export_enabled": bool(data.get("dailyExportEnabled")),
        "streaming_export_enabled": bool(data.get("streamingExportEnabled")),
        "fresh_daily_export_enabled": bool(data.get("freshDailyExportEnabled")),
    }


def list_properties(token):
    """The picker's list. Metadata for a chosen property comes from get_property."""
    return list_account_summaries(token)


def _property_path(property_id):
    raw = str(property_id or "").strip()
    if raw.startswith("properties/"):
        return raw
    if not raw:
        raise GoogleApiError("Choose a Google Analytics property first.")
    return "properties/" + raw


def list_accounts(token):
    """Google Analytics accounts the grant can create a property under."""
    data = _get(token, BASE + "/accounts?pageSize=200")
    return [{
        "account": item.get("name") or "",
        "account_id": str(item.get("name") or "").split("/")[-1],
        "display_name": item.get("displayName") or "",
    } for item in (data.get("accounts") or [])]


def create_property(token, account, display_name, time_zone="Etc/UTC",
                    currency_code="USD"):
    """Create a GA4 property under an existing Analytics account."""
    name = str(display_name or "").strip()
    if not name:
        raise GoogleApiError("Give the new property a name.")
    parent = str(account or "").strip()
    if not parent:
        raise GoogleApiError("Choose the Google Analytics account that will own it.")
    if not parent.startswith("accounts/"):
        parent = "accounts/" + parent
    data = _post(token, BASE + "/properties", {
        "parent": parent,
        "displayName": name[:100],
        "timeZone": (time_zone or "Etc/UTC").strip(),
        "currencyCode": (currency_code or "USD").strip().upper()[:3],
    })
    return {
        "property": data.get("name") or "",
        "property_id": str(data.get("name") or "").split("/")[-1],
        "display_name": data.get("displayName") or name,
        "account": data.get("account") or parent,
        "time_zone": data.get("timeZone") or time_zone,
        "currency_code": data.get("currencyCode") or currency_code,
    }


def create_web_stream(token, property_id, display_name="Website",
                      default_uri=""):
    """Create the web data stream and return its measurement ID."""
    data = _post(token, BASE + "/" + _property_path(property_id) + "/dataStreams", {
        "type": "WEB_DATA_STREAM",
        "displayName": (display_name or "Website")[:100],
        "webStreamData": {"defaultUri": (default_uri or "").strip()[:2000]},
    })
    web = data.get("webStreamData") or {}
    return {
        "stream": data.get("name") or "",
        "stream_id": str(data.get("name") or "").split("/")[-1],
        "measurement_id": web.get("measurementId") or "",
        "default_uri": web.get("defaultUri") or default_uri,
    }


def create_key_event(token, property_id, event_name, counting_method="ONCE_PER_EVENT"):
    """Mark an event as a key event on the property."""
    name = str(event_name or "").strip()
    if not name:
        raise GoogleApiError("A key event needs an event name.")
    data = _post(token, BASE + "/" + _property_path(property_id) + "/keyEvents", {
        "eventName": name[:100],
        "countingMethod": (counting_method or "ONCE_PER_EVENT").strip(),
    })
    return {
        "key_event": data.get("name") or "",
        "event_name": data.get("eventName") or name,
        "counting_method": data.get("countingMethod") or counting_method,
    }


def create_event_audience(token, property_id, display_name, event_name,
                          membership_days=30):
    """Create a simple audience of users who triggered one event.

    The Admin API's audience filter language is large; this is the smallest useful
    expression and is deliberately one event, not a predicate builder.
    """
    name = str(display_name or "").strip()
    event = str(event_name or "").strip()
    if not name or not event:
        raise GoogleApiError("An audience needs a name and an event.")
    payload = {
        "displayName": name[:100],
        "description": "Created by AscendPlatform from the %s event." % event,
        "membershipDurationDays": max(1, min(540, int(membership_days or 30))),
        "filterClauses": [{
            "clauseType": "INCLUDE",
            "simpleFilter": {
                "scope": "AUDIENCE_FILTER_SCOPE_WITHIN_SAME_EVENT",
                "filterExpression": {
                    "andGroup": {
                        "filterExpressions": [{
                            "eventFilter": {"eventName": event[:100]},
                        }],
                    },
                },
            },
        }],
    }
    data = _post(token, BASE_ALPHA + "/" + _property_path(property_id) + "/audiences",
                 payload)
    return {
        "audience": data.get("name") or "",
        "display_name": data.get("displayName") or name,
        "event_name": event,
    }
