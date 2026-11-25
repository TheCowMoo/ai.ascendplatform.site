"""Read-only Google Tag Manager discovery.

Phase 1 lists the accounts and web containers the Google grant can reach, so a client
can attach an existing container instead of creating a duplicate. Creating containers,
tags and versions is a later, approval-gated phase.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://www.googleapis.com/tagmanager/v2"


class GtmApiError(Exception):
    """A Tag Manager problem phrased for the person connecting the account."""


def _transport(url, headers=None, timeout=20):
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
    if status == 403:
        raise GtmApiError(
            "Google does not grant Tag Manager access to this connection. Reconnect "
            "Google and approve the Tag Manager permission."
        )
    if status >= 400:
        raise GtmApiError("Google Tag Manager returned %s: %s"
                          % (status, str(body)[:300]))
    try:
        return json.loads(body)
    except ValueError:
        raise GtmApiError("Google Tag Manager returned an unreadable response.")


def list_accounts(token):
    data = _get(token, BASE + "/accounts")
    return [{
        "account_id": str(item.get("accountId") or ""),
        "name": item.get("name") or "",
        "path": item.get("path") or "",
    } for item in (data.get("account") or [])]


def list_containers(token):
    """Accounts and their containers, flattened for a picker."""
    rows = []
    for account in list_accounts(token):
        account_id = account["account_id"]
        if not account_id:
            continue
        url = (BASE + "/accounts/" + urllib.parse.quote(account_id)
               + "/containers")
        data = _get(token, url)
        for item in data.get("container") or []:
            usage = item.get("usageContext") or []
            if usage and "web" not in usage:
                continue
            rows.append({
                "account_id": account_id,
                "account_name": account["name"],
                "container_id": str(item.get("containerId") or ""),
                "public_id": item.get("publicId") or "",
                "name": item.get("name") or "",
                "domain_name": item.get("domainName") or "",
                "usage_context": usage,
                "path": item.get("path") or "",
            })
    return rows
