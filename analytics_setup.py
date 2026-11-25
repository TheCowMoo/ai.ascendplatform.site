"""Orchestrate the GA4 + Tag Manager setup write path.

This is deliberately separate from the Flask route: the route resolves the tenant,
checks the write scopes and hands over a token. Every Google call and audit record lives
here, so it can be tested without an HTTP client.
"""

import analytics as analytics_service
import analytics_oauth
import ga4_admin
import gtm_admin
import tag_install


class SetupError(Exception):
    """A setup step failed. ``partial`` carries anything already created."""

    def __init__(self, message, partial=None):
        super().__init__(message)
        self.partial = partial or {}


def _record(org, action, actor, target="", payload=None, status="ok", result=""):
    analytics_service.record_action(
        org, action, target=target, payload=payload, status=status,
        result=result, actor=actor)


def setup(org, scope, token, connection, actor, data, site=None):
    """Create the property, stream, key events/audience, container and site tag.

    Raises ``SetupError`` if the property or its web stream cannot be created. Container
    problems are returned in ``errors`` because the property already exists and can be
    retried without creating a second one.
    """
    data = data or {}
    make_container = bool(data.get("make_container"))
    gtm_account = str(data.get("gtm_account_id") or "").strip().rstrip("/").split("/")[-1]
    if make_container and not gtm_account:
        raise SetupError("Choose the Tag Manager account that will own the container.")
    default_uri = str(data.get("default_uri") or "").strip()
    if not default_uri and site:
        default_uri = "https://" + site["domain"]
    property_name = str(data.get("property_name") or
                        (site["domain"] if site else "New property")).strip()[:100]
    account = str(data.get("account") or "").strip()
    time_zone = str(data.get("time_zone") or "Etc/UTC").strip()
    currency = str(data.get("currency_code") or "USD").strip().upper()[:3]
    key_events = [name.strip() for name in
                  str(data.get("key_events") or "form_submit").split(",")
                  if name.strip()][:12]
    payload = {"org_id": org, "connection_scope": scope,
               "property_name": property_name, "account": account,
               "site_id": (site or {}).get("id")}
    try:
        prop = ga4_admin.create_property(
            token, account, property_name, time_zone, currency)
    except ga4_admin.GoogleApiError as exc:
        _record(org, "ga4_create_property", actor, status="error", result=str(exc),
                payload=payload)
        raise SetupError(str(exc))
    _record(org, "ga4_create_property", actor, target=prop["property"],
            payload=payload, result=prop["property"])
    try:
        stream = ga4_admin.create_web_stream(
            token, prop["property"], property_name, default_uri)
    except ga4_admin.GoogleApiError as exc:
        _record(org, "ga4_create_stream", actor, target=prop["property"],
                status="error", result=str(exc), payload=payload)
        raise SetupError(str(exc), {"property": prop})
    _record(org, "ga4_create_stream", actor, target=prop["property"],
            payload=payload, result=stream.get("measurement_id") or "")

    existing = next(
        (row for row in analytics_service.sources(org)
         if row.get("kind") == "ga4"
         and row.get("external_id") == prop["property"]), None)
    old_connection_id = (analytics_oauth.connection_id_from_source(existing)
                         if existing else None)
    try:
        source = analytics_service.save_source({
            "kind": "ga4",
            "external_id": prop["property"],
            "label": property_name,
            "credential_ref": analytics_oauth.source_marker(connection["id"]),
        }, source_id=existing["id"] if existing else None, org_id=org)
        if old_connection_id and old_connection_id != connection["id"]:
            analytics_oauth.drop_if_unused(org, old_connection_id)
        property_row = analytics_service.save_property({
            "org_id": org, "source_id": source["id"],
            "property_id": prop["property_id"],
            "display_name": prop["display_name"],
            "account_name": data.get("account_name") or account,
            "time_zone": prop.get("time_zone") or time_zone,
            "currency_code": prop.get("currency_code") or currency,
            "tag_container_id": None,
        })
    except analytics_service.SourceError as exc:
        raise SetupError(str(exc), {"property": prop, "stream": stream})

    errors = []
    key_results = []
    for event_name in key_events:
        try:
            key_results.append(ga4_admin.create_key_event(
                token, prop["property"], event_name))
            _record(org, "ga4_create_key_event", actor, target=event_name,
                    payload=payload)
        except ga4_admin.GoogleApiError as exc:
            errors.append("Key event %s: %s" % (event_name, exc))
            _record(org, "ga4_create_key_event", actor, target=event_name,
                    status="error", result=str(exc), payload=payload)

    audience = None
    audience_event = str(data.get("audience_event") or "").strip()
    if audience_event:
        try:
            audience = ga4_admin.create_event_audience(
                token, prop["property"],
                data.get("audience_name") or ("%s audience" % audience_event),
                audience_event, data.get("audience_days") or 30)
            _record(org, "ga4_create_audience", actor, target=audience_event,
                    payload=payload)
        except ga4_admin.GoogleApiError as exc:
            errors.append("Audience: %s" % exc)
            _record(org, "ga4_create_audience", actor, target=audience_event,
                    status="error", result=str(exc), payload=payload)

    container = None
    if make_container:
        try:
            container = gtm_admin.setup_ga4_container(
                token, gtm_account,
                data.get("container_name") or property_name,
                (site or {}).get("domain") or default_uri,
                stream.get("measurement_id") or "",
                tuple(key_events or ["form_submit"]))
            saved_container = analytics_service.save_container({
                "org_id": org, "connection_id": connection["id"],
                "account_id": gtm_account,
                "container_id": container["container_id"],
                "public_id": container["public_id"],
                "name": container["name"],
                "domain_name": (site or {}).get("domain") or default_uri,
                "kind": "web",
                "last_published_version": container.get("version_id") or "",
            })
            property_row = analytics_service.save_property({
                "org_id": org, "source_id": source["id"],
                "property_id": prop["property_id"],
                "display_name": prop["display_name"],
                "account_name": data.get("account_name") or account,
                "time_zone": prop.get("time_zone") or time_zone,
                "currency_code": prop.get("currency_code") or currency,
                "tag_container_id": saved_container["id"],
            })
            container["db_id"] = saved_container["id"]
            _record(org, "gtm_create_container", actor,
                    target=container["container_id"], payload=payload,
                    result=container.get("public_id") or "")
        except (gtm_admin.GtmApiError, analytics_service.SourceError) as exc:
            errors.append("Tag Manager: %s" % exc)
            _record(org, "gtm_create_container", actor, status="error",
                    result=str(exc), payload=payload)

    tag_result = None
    if container and site and container.get("public_id"):
        try:
            tag_result = tag_install.install_for_site(site, container["public_id"])
            _record(org, "gtm_install_snippet", actor,
                    target=site.get("domain") or "",
                    payload=payload, status="ok" if tag_result.get("ok") else "error",
                    result="%s file(s), %s updated" % (tag_result.get("files"),
                                                      tag_result.get("updated")))
            if not tag_result.get("ok"):
                errors.extend(tag_result.get("errors") or [])
        except Exception as exc:
            errors.append("Tag install: %s" % exc)
            _record(org, "gtm_install_snippet", actor,
                    target=site.get("domain") or "", status="error",
                    result=str(exc), payload=payload)

    return {
        "ok": not errors,
        "property": property_row,
        "stream": stream,
        "key_events": key_results,
        "audience": audience,
        "container": container,
        "tag": tag_result,
        "source": source,
        "errors": errors,
    }