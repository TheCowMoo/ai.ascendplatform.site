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


def _transport(url, headers=None, timeout=20, method="GET", payload=None):
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


def _get(token, url):
    return _request(token, url)


def _post(token, url, payload):
    return _request(token, url, method="POST", payload=payload)


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


def _container_url(account_id, container_id, suffix=""):
    return (BASE + "/accounts/" + urllib.parse.quote(str(account_id))
            + "/containers/" + urllib.parse.quote(str(container_id)) + suffix)


def create_container(token, account_id, name, domain_name=""):
    """Create a web container under an existing GTM account."""
    label = str(name or "").strip()
    if not label:
        raise GtmApiError("Give the Tag Manager container a name.")
    if not str(account_id or "").strip():
        raise GtmApiError("Choose the Tag Manager account that will own it.")
    payload = {"name": label[:100], "usageContext": ["web"]}
    if (domain_name or "").strip():
        payload["domainName"] = [domain_name.strip()[:2000]]
    data = _post(token, BASE + "/accounts/" + urllib.parse.quote(str(account_id))
                 + "/containers", payload)
    returned_domains = data.get("domainName")
    if isinstance(returned_domains, list):
        returned_domains = returned_domains[0] if returned_domains else ""
    return {
        "account_id": str(account_id),
        "container_id": str(data.get("containerId") or ""),
        "public_id": data.get("publicId") or "",
        "name": data.get("name") or label,
        "domain_name": returned_domains or domain_name,
        "path": data.get("path") or "",
    }


def default_workspace(token, account_id, container_id):
    data = _get(token, _container_url(account_id, container_id) + "/workspaces")
    workspaces = data.get("workspace") or []
    if not workspaces:
        raise GtmApiError("That Tag Manager container has no workspace.")
    workspace = workspaces[0]
    return {
        "workspace_id": str(workspace.get("workspaceId") or ""),
        "name": workspace.get("name") or "Default Workspace",
    }


def _workspace_url(account_id, container_id, workspace_id, suffix=""):
    return (_container_url(account_id, container_id) + "/workspaces/"
            + urllib.parse.quote(str(workspace_id)) + suffix)


def create_pageview_trigger(token, account_id, container_id, workspace_id,
                            name="All Pages"):
    data = _post(token, _workspace_url(account_id, container_id, workspace_id)
                 + "/triggers", {"name": name[:100], "type": "pageview"})
    return {"trigger_id": str(data.get("triggerId") or ""),
            "name": data.get("name") or name}


def create_custom_event_trigger(token, account_id, container_id, workspace_id,
                                event_name, name=None):
    event = str(event_name or "").strip()
    if not event:
        raise GtmApiError("A custom event trigger needs an event name.")
    payload = {
        "name": (name or ("Custom event: %s" % event))[:100],
        "type": "customEvent",
        "customEventFilter": [{
            "type": "equals",
            "parameter": [
                {"type": "template", "key": "arg0", "value": "{{_event}}"},
                {"type": "template", "key": "arg1", "value": event[:100]},
            ],
        }],
    }
    data = _post(token, _workspace_url(account_id, container_id, workspace_id)
                 + "/triggers", payload)
    return {"trigger_id": str(data.get("triggerId") or ""),
            "name": data.get("name") or payload["name"]}


def create_ga4_config_tag(token, account_id, container_id, workspace_id,
                          measurement_id, trigger_id, name="GA4 configuration"):
    measurement = str(measurement_id or "").strip()
    if not measurement:
        raise GtmApiError("A GA4 configuration tag needs a measurement ID.")
    payload = {
        "name": name[:100],
        "type": "gaawc",
        "parameter": [
            {"type": "template", "key": "measurementId", "value": measurement},
            {"type": "boolean", "key": "sendPageView", "value": "true"},
        ],
        "firingTriggerId": [str(trigger_id)],
    }
    data = _post(token, _workspace_url(account_id, container_id, workspace_id)
                 + "/tags", payload)
    return {"tag_id": str(data.get("tagId") or ""),
            "name": data.get("name") or name}


def create_ga4_event_tag(token, account_id, container_id, workspace_id,
                         measurement_id, event_name, trigger_id, name=None):
    measurement = str(measurement_id or "").strip()
    event = str(event_name or "").strip()
    if not measurement or not event:
        raise GtmApiError("A GA4 event tag needs a measurement ID and an event name.")
    payload = {
        "name": (name or ("GA4 event: %s" % event))[:100],
        "type": "gaawe",
        "parameter": [
            {"type": "template", "key": "measurementId", "value": measurement},
            {"type": "template", "key": "eventName", "value": event[:100]},
        ],
        "firingTriggerId": [str(trigger_id)],
    }
    data = _post(token, _workspace_url(account_id, container_id, workspace_id)
                 + "/tags", payload)
    return {"tag_id": str(data.get("tagId") or ""),
            "name": data.get("name") or payload["name"]}


def create_version(token, account_id, container_id, workspace_id,
                   name="AscendPlatform setup", notes=""):
    data = _post(token, _workspace_url(account_id, container_id, workspace_id)
                 + ":create_version", {
                     "name": name[:100],
                     "notes": (notes or "")[:1000],
                 })
    if data.get("compilerError"):
        raise GtmApiError(
            "Google Tag Manager reported a compiler error for the new version, so it "
            "was not published. Open the container and fix the highlighted tag.")
    version = data.get("containerVersion") or {}
    return {
        "version_id": str(version.get("containerVersionId") or ""),
        "name": version.get("name") or name,
    }


def publish_version(token, account_id, container_id, version_id):
    data = _request(token, _container_url(account_id, container_id) + "/versions/"
                    + urllib.parse.quote(str(version_id)) + ":publish", method="POST")
    version = data.get("containerVersion") or {}
    return {
        "version_id": str(version.get("containerVersionId") or version_id),
        "name": version.get("name") or "",
    }


def setup_ga4_container(token, account_id, container_name, domain_name,
                        measurement_id, event_names=("form_submit",)):
    """Create a web container, GA4 config/event tags, a version and publish it."""
    container = create_container(token, account_id, container_name, domain_name)
    if not container.get("container_id"):
        raise GtmApiError("Google Tag Manager did not return a container id.")
    workspace = default_workspace(token, account_id, container["container_id"])
    pages_trigger = create_pageview_trigger(
        token, account_id, container["container_id"], workspace["workspace_id"])
    config_tag = create_ga4_config_tag(
        token, account_id, container["container_id"], workspace["workspace_id"],
        measurement_id, pages_trigger["trigger_id"])
    event_tags = []
    for event_name in event_names or ():
        trigger = create_custom_event_trigger(
            token, account_id, container["container_id"],
            workspace["workspace_id"], event_name)
        tag = create_ga4_event_tag(
            token, account_id, container["container_id"],
            workspace["workspace_id"], measurement_id, event_name,
            trigger["trigger_id"])
        event_tags.append({"event": event_name, "trigger": trigger, "tag": tag})
    version = create_version(
        token, account_id, container["container_id"], workspace["workspace_id"],
        name="AscendPlatform setup",
        notes="GA4 configuration and events for %s" % (domain_name or "the site"))
    published = publish_version(
        token, account_id, container["container_id"], version["version_id"])
    container["workspace_id"] = workspace["workspace_id"]
    container["version_id"] = published.get("version_id") or version["version_id"]
    container["tags"] = [config_tag] + [row["tag"] for row in event_tags]
    container["triggers"] = [pages_trigger] + [row["trigger"] for row in event_tags]
    return container
