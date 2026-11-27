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


def _create_container(org, token, connection, actor, data, property_row, source,
                      prop, stream, site, key_events, payload, errors):
    """Create, store and publish one GTM container for an existing property."""
    gtm_account = str(data.get("gtm_account_id") or "").strip().rstrip("/").split("/")[-1]
    if not gtm_account:
        raise SetupError("Choose the Tag Manager account that will own the container.")
    measurement_id = str((stream or {}).get("measurement_id") or "").strip()
    if not measurement_id:
        raise SetupError("Google did not return a web-stream measurement ID, so a "
                         "Tag Manager container cannot be linked.")
    property_name = str(prop.get("display_name") or
                        (site["domain"] if site else "Website")).strip()[:100]
    default_uri = str((stream or {}).get("default_uri") or "").strip()
    if not default_uri and site:
        default_uri = "https://" + site["domain"]
    domain = (site or {}).get("domain") or default_uri
    container = gtm_admin.setup_ga4_container(
        token, gtm_account,
        data.get("container_name") or property_name,
        domain, measurement_id,
        tuple(key_events or ["form_submit"]))
    saved_container = analytics_service.save_container({
        "org_id": org, "connection_id": connection["id"],
        "account_id": gtm_account,
        "container_id": container["container_id"],
        "public_id": container["public_id"],
        "name": container["name"],
        "domain_name": domain,
        "kind": "web",
        "last_published_version": container.get("version_id") or "",
    })
    updated = dict(property_row or {})
    updated.update({
        "org_id": org, "source_id": source["id"],
        "property_id": prop.get("property_id") or "",
        "display_name": prop.get("display_name") or property_name,
        "account_name": data.get("account_name") or prop.get("account") or "",
        "time_zone": prop.get("time_zone") or "",
        "currency_code": prop.get("currency_code") or "",
        "tag_container_id": saved_container["id"],
    })
    analytics_service.save_property(updated)
    container["db_id"] = saved_container["id"]
    _record(org, "gtm_create_container", actor,
            target=container["container_id"], payload=payload,
            result=container.get("public_id") or "")
    return container


def _install_tag(org, actor, data, site, container, payload, errors):
    """Put the container snippet on one site, recording the outcome."""
    if not (container and site and container.get("public_id")):
        return None
    try:
        tag_result = tag_install.install_for_site(site, container["public_id"])
        _record(org, "gtm_install_snippet", actor,
                target=site.get("domain") or "", payload=payload,
                status="ok" if tag_result.get("ok") else "error",
                result="%s file(s), %s updated" % (tag_result.get("files"),
                                                  tag_result.get("updated")))
        if not tag_result.get("ok"):
            errors.extend(tag_result.get("errors") or [])
        return tag_result
    except Exception as exc:
        errors.append("Tag install: %s" % exc)
        _record(org, "gtm_install_snippet", actor,
                target=site.get("domain") or "", status="error",
                result=str(exc), payload=payload)
        return None


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

    # Store the source and property before creating the stream. If Google fails halfway,
    # the next click finds the property and completes it instead of creating a duplicate.
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
        raise SetupError(str(exc), {"property": prop})

    try:
        stream = ga4_admin.create_web_stream(
            token, prop["property"], property_name, default_uri)
    except ga4_admin.GoogleApiError as exc:
        _record(org, "ga4_create_stream", actor, target=prop["property"],
                status="error", result=str(exc), payload=payload)
        raise SetupError(str(exc), {"property": prop})
    _record(org, "ga4_create_stream", actor, target=prop["property"],
            payload=payload, result=stream.get("measurement_id") or "")

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
            errors.append("Audience skipped: %s. The property and container are still "
                          "ready." % exc)
            _record(org, "ga4_create_audience", actor, target=audience_event,
                    status="error", result=str(exc), payload=payload)

    container = None
    if make_container:
        try:
            container = _create_container(
                org, token, connection, actor, data, property_row, source,
                prop, stream, site, key_events, payload, errors)
            property_row = analytics_service.property_for_source(source["id"])
        except (SetupError, gtm_admin.GtmApiError, analytics_service.SourceError) as exc:
            errors.append("Tag Manager: %s" % exc)
            _record(org, "gtm_create_container", actor, status="error",
                    result=str(exc), payload=payload)

    tag_result = _install_tag(org, actor, data, site, container, payload, errors)

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
        "needs": ([] if container or not make_container else ["gtm_account"]),
    }


def _event_names(data):
    return [name.strip() for name in
            str(data.get("key_events") or "form_submit").split(",")
            if name.strip()][:12]



def ensure_setup(org, scope, token, connection, actor, data, site=None):
    """Complete setup without creating a second property or container.

    The first click creates everything. Every later click re-reads the stored rows and
    the stream/container metadata, then fills only what is missing. Google's account
    ticket remains a separate browser step because Google requires the person to accept
    its own terms.
    """
    data = data or {}
    make_container = bool(data.get("make_container", True))
    gtm_account = str(data.get("gtm_account_id") or "").strip().rstrip("/").split("/")[-1]
    properties = analytics_service.properties(org)
    # Multiple GA4 sources can exist. Reuse only the property attached to the Google
    # grant that is running this setup; otherwise one click could tag the wrong site.
    connection_id = (connection or {}).get("id")
    ga_sources = [row for row in analytics_service.sources(org)
                  if row.get("kind") == "ga4"]
    source_ids = {
        row.get("id") for row in ga_sources
        if analytics_oauth.connection_id_from_source(row) == connection_id
    }
    if ga_sources:
        properties = [row for row in properties
                      if row.get("source_id") in source_ids]
    if not properties:
        result = setup(org, scope, token, connection, actor, data, site)
        result["mode"] = "created"
        if make_container and not result.get("container"):
            result.setdefault("needs", [])
            if "gtm_account" not in result["needs"]:
                result["needs"].append("gtm_account")
        return result

    property_row = properties[0]
    source = analytics_service.get_source(property_row.get("source_id"))
    if not source or source.get("org_id") != org or source.get("kind") != "ga4":
        raise SetupError("The stored GA4 property is not attached to this workspace. "
                         "Reconnect Google, then try again.")
    property_id = str(property_row.get("property_id") or "").split("/")[-1]
    if not property_id:
        raise SetupError("The stored GA4 property has no property id. Reconnect it.")
    prop = {
        "property": "properties/" + property_id,
        "property_id": property_id,
        "display_name": property_row.get("display_name") or
                        (site["domain"] if site else "Website"),
        "account": property_row.get("account_name") or "",
        "account_name": property_row.get("account_name") or "",
        "time_zone": property_row.get("time_zone") or "",
        "currency_code": property_row.get("currency_code") or "",
    }
    default_uri = str(data.get("default_uri") or "").strip()
    if not default_uri and site:
        default_uri = "https://" + site["domain"]
    property_name = str(data.get("property_name") or prop["display_name"] or
                        (site["domain"] if site else "Website")).strip()[:100]
    key_events = _event_names(data)
    payload = {"org_id": org, "connection_scope": scope,
               "property_name": property_name, "account": prop["account"],
               "site_id": (site or {}).get("id")}
    errors = []
    created = []
    needs = []

    # A previous attempt can have created the property and failed before the stream.
    try:
        streams = ga4_admin.list_web_streams(token, property_id)
    except ga4_admin.GoogleApiError as exc:
        errors.append("Data streams: %s" % exc)
        streams = []
    stream = next((row for row in streams if row.get("measurement_id")), None)
    if not stream:
        try:
            stream = ga4_admin.create_web_stream(
                token, prop["property"], property_name, default_uri)
            created.append("web stream")
            _record(org, "ga4_create_stream", actor, target=prop["property"],
                    payload=payload, result=stream.get("measurement_id") or "")
        except ga4_admin.GoogleApiError as exc:
            _record(org, "ga4_create_stream", actor, target=prop["property"],
                    status="error", result=str(exc), payload=payload)
            raise SetupError(str(exc), {"property": prop, "source": source})

    # Key events and the audience are additive. List first so a second click does not
    # turn an idempotent setup into a page full of "already exists" errors.
    existing_events = set()
    try:
        existing_events = {row.get("event_name") for row in
                           ga4_admin.list_key_events(token, property_id)}
    except ga4_admin.GoogleApiError as exc:
        errors.append("Key events: %s" % exc)
    key_results = []
    for event_name in key_events:
        if event_name in existing_events:
            continue
        try:
            key_results.append(ga4_admin.create_key_event(
                token, prop["property"], event_name))
            created.append("key event %s" % event_name)
            _record(org, "ga4_create_key_event", actor, target=event_name,
                    payload=payload)
        except ga4_admin.GoogleApiError as exc:
            errors.append("Key event %s: %s" % (event_name, exc))
            _record(org, "ga4_create_key_event", actor, target=event_name,
                    status="error", result=str(exc), payload=payload)

    audience = None
    audience_event = str(data.get("audience_event") or "").strip()
    if audience_event:
        audience_name = data.get("audience_name") or ("%s audience" % audience_event)
        existing_audiences = set()
        try:
            existing_audiences = {row.get("display_name") for row in
                                 ga4_admin.list_event_audiences(token, property_id)}
        except ga4_admin.GoogleApiError as exc:
            errors.append("Audiences: %s" % exc)
        if audience_name not in existing_audiences:
            try:
                audience = ga4_admin.create_event_audience(
                    token, prop["property"], audience_name, audience_event,
                    data.get("audience_days") or 30)
                created.append("audience")
                _record(org, "ga4_create_audience", actor, target=audience_event,
                        payload=payload)
            except ga4_admin.GoogleApiError as exc:
                errors.append("Audience skipped: %s. The property and container are "
                              "still ready." % exc)
                _record(org, "ga4_create_audience", actor, target=audience_event,
                        status="error", result=str(exc), payload=payload)


    container = None
    container_rows = analytics_service.containers(org)
    linked_id = property_row.get("tag_container_id")
    if linked_id:
        container = next((row for row in container_rows
                          if str(row.get("id")) == str(linked_id)), None)
    if not container and data.get("container_id"):
        container = next((row for row in container_rows
                          if str(row.get("container_id")) == str(data["container_id"])),
                         None)
        if container:
            updated = dict(property_row)
            updated.update({"org_id": org, "source_id": source["id"],
                            "tag_container_id": container["id"]})
            property_row = analytics_service.save_property(updated)
            created.append("linked existing container")
    if not container and make_container:
        if not gtm_account:
            needs.append("gtm_account")
            errors.append("Create a Google Tag Manager account, then run setup again "
                          "to create and publish the container.")
        elif not str((stream or {}).get("measurement_id") or "").strip():
            needs.append("gtm_account")
            errors.append("The GA4 web stream has no measurement ID, so the container "
                          "cannot be linked. Create a web stream in Google Analytics.")
        else:
            try:
                container = _create_container(
                    org, token, connection, actor, data, property_row, source,
                    prop, stream, site, key_events, payload, errors)
                property_row = analytics_service.property_for_source(source["id"])
                created.append("Tag Manager container")
            except (SetupError, gtm_admin.GtmApiError, analytics_service.SourceError) as exc:
                errors.append("Tag Manager: %s" % exc)
                _record(org, "gtm_create_container", actor, status="error",
                        result=str(exc), payload=payload)

    tag_result = _install_tag(org, actor, data, site, container, payload, errors)

    return {
        "ok": not errors,
        "mode": "existing",
        "property": property_row,
        "source": source,
        "stream": stream,
        "key_events": key_results,
        "audience": audience,
        "container": container,
        "tag": tag_result,
        "errors": errors,
        "created": created,
        "needs": needs,
    }
