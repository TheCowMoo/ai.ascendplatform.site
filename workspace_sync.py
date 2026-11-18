"""Mirror AscendPlatform workspace records into a GHL custom object.

Contacts are mirrored two-way by ``contact_sync``. This module handles the objects that
are not contacts: forms, content drafts, automations, sites and form responses. They are
stored as records of one namespaced custom object so a client can see the workspace in
GHL without inventing a different native GHL shape for each Ascend concept.

The mirror is outbound: a GHL edit to one of these records is visible there, but it is
not allowed to overwrite the source document. Contacts remain the two-way surface.
"""

import hashlib
import json
import urllib.parse

import automations as automations_service
import deliverables as deliverables_service
import forms as forms_service
import platform_mail
import sites as sites_service
from db import get_connection, now_str


class MirrorError(ValueError):
    """A workspace-object mirror failure suitable for the operator."""


SCHEMA_KEY = "custom_objects.ascendplatform_item"
OBJECT_KEY = "ascendplatform_item"
OBJECT_LABEL = "AscendPlatform item"

# The record fields. ``title`` is the primary display property required by the
# custom-object schema; the rest are namespaced by the object itself.
FIELDS = (
    ("title", "Title", "TEXT"),
    ("kind", "Kind", "TEXT"),
    ("local_id", "Local ID", "TEXT"),
    ("status", "Status", "TEXT"),
    ("url", "URL", "TEXT"),
    ("summary", "Summary", "LARGE_TEXT"),
    ("content", "Content", "LARGE_TEXT"),
    ("metadata", "Metadata", "LARGE_TEXT"),
    ("updated_at", "Updated at", "TEXT"),
)
MAX_RECORDS = 3000
TEXT_LIMIT = 60000


def _safe_error(exc):
    text = str(exc or "Workspace mirror failed").strip()
    for name in ("GoHighLevel", "HighLevel", "GHL"):
        text = text.replace(name, "AscendPlatform")
    return text[:700]


def _json(raw, fallback):
    try:
        return json.loads(raw or "") if isinstance(raw, str) else (raw or fallback)
    except (TypeError, ValueError):
        return fallback


def _limit(value):
    text = value if isinstance(value, str) else json.dumps(value or {}, default=str)
    if len(text) <= TEXT_LIMIT:
        return text
    return text[:TEXT_LIMIT] + chr(10) + "[record truncated by AscendPlatform]"


def _schema_location(org_id):
    return platform_mail.connection(org_id)["location"]


def _api(method, path, payload=None, org_id=None):
    """Object endpoints live on GHL's v3 surface; mail stays on its configured version."""
    return platform_mail.api_call(method, path, payload=payload, org_id=org_id,
                                  version="v3")


def _objects(org_id):
    location = _schema_location(org_id)
    body = _api(
        "GET", "/objects?" + urllib.parse.urlencode({"locationId": location}),
        org_id=org_id,
    )
    return body.get("objects") or body.get("object") or []


def _schema(org_id):
    location = _schema_location(org_id)
    path = "/objects/%s?" % urllib.parse.quote(SCHEMA_KEY, safe=".") +         urllib.parse.urlencode({"locationId": location, "fetchProperties": "true"})
    return _api("GET", path, org_id=org_id)


def _field_keys(schema):
    fields = schema.get("fields") or schema.get("object", {}).get("fields") or []
    keys = set()
    for field in fields:
        key = str(field.get("fieldKey") or field.get("name") or "")
        keys.add(key.rsplit(".", 1)[-1])
    return keys


def ensure_schema(org_id):
    """Create the namespaced custom object and its fields if they do not exist."""
    location = _schema_location(org_id)
    objects = _objects(org_id)
    found = False
    for item in objects:
        if str(item.get("key") or "") in (SCHEMA_KEY, OBJECT_KEY):
            found = True
            break
    warnings = []
    if not found:
        body = _api(
            "POST", "/objects", {
                "labels": {"singular": OBJECT_LABEL, "plural": OBJECT_LABEL + "s"},
                "key": OBJECT_KEY,
                "description": "AscendPlatform workspace records mirrored into this location.",
                "locationId": location,
                "primaryDisplayPropertyDetails": {
                    "key": "title", "name": "Title", "dataType": "TEXT",
                },
            }, org_id=org_id,
        )
        created = body.get("object") or body
        if not (created.get("key") or created.get("id")):
            raise MirrorError("AscendPlatform could not create the workspace object schema.")

    schema = _schema(org_id)
    keys = _field_keys(schema)
    for key, name, data_type in FIELDS:
        if key in keys:
            continue
        payload = {
            "locationId": location,
            "name": name,
            "description": "AscendPlatform mirror field: %s" % name,
            "dataType": data_type,
            "fieldKey": "custom_object.%s.%s" % (OBJECT_KEY, key),
            "objectKey": "custom_object.%s" % OBJECT_KEY,
            "showInForms": False,
            "parentId": "",
        }
        try:
            _api("POST", "/custom-fields", payload, org_id=org_id)
        except Exception as exc:
            warnings.append("AscendPlatform could not create the %s field: %s"
                            % (name, _safe_error(exc)))
    return {"schema_key": SCHEMA_KEY, "warnings": warnings}


def _item(object_kind, local_id, title, kind, status, url="", summary="",
          content="", metadata=None, updated_at=""):
    return {
        "object_kind": object_kind,
        "local_id": int(local_id),
        "title": str(title or "Untitled item")[:250],
        "kind": str(kind or object_kind)[:80],
        "status": str(status or "")[:80],
        "url": str(url or "")[:1000],
        "summary": str(summary or "")[:4000],
        "content": _limit(content or ""),
        "metadata": _limit(metadata or {}),
        "updated_at": str(updated_at or ""),
    }


def collect_items(org_id):
    """Every workspace record that should be visible in the external mirror."""
    items = []
    forms = forms_service.list_forms(org_id, limit=200)
    for form in forms:
        link = forms_service.link_for_org(form, org_id)
        items.append(_item(
            "form", form["id"], form.get("name") or "Untitled form",
            form.get("kind") or "form", form.get("status") or "draft",
            url=link,
            summary="%d question%s ? %d response%s" % (
                form.get("question_count") or 0,
                "" if (form.get("question_count") or 0) == 1 else "s",
                form.get("responses") or 0,
                "" if (form.get("responses") or 0) == 1 else "s"),
            content={"questions": form.get("questions") or [],
                     "settings": form.get("settings") or {}},
            metadata={"local_id": form["id"], "slug": form.get("slug") or "",
                      "page_count": form.get("page_count") or 1},
            updated_at=form.get("updated_at") or form.get("created_at") or "",
        ))
        for response in forms_service.list_responses(form["id"], org_id, limit=200):
            answers = response.get("answers") or {}
            items.append(_item(
                "form_response", response["id"],
                "%s response #%s" % (form.get("name") or "Form", response["id"]),
                "form_response", "received", summary="%s" % (form.get("name") or "Form"),
                content={"answers": answers, "score": response.get("score") or 0,
                         "max_score": response.get("max_score") or 0,
                         "outcome": response.get("outcome") or ""},
                metadata={"form_id": form["id"], "contact_id": response.get("contact_id"),
                          "submitted_at": response.get("created_at") or ""},
                updated_at=response.get("created_at") or "",
            ))

    for item in deliverables_service.list_deliverables(None, org_id, limit=500):
        items.append(_item(
            "deliverable", item["id"], item.get("title") or "Untitled content",
            item.get("kind") or "content", item.get("status") or "draft",
            url=item.get("url") or "", summary=item.get("summary") or "",
            content=item.get("content") or "",
            metadata={"local_id": item["id"], "slug": item.get("slug") or "",
                      "site_id": item.get("site_id")},
            updated_at=item.get("updated_at") or item.get("created_at") or "",
        ))

    for site in sites_service.list_sites(org_id):
        items.append(_item(
            "site", site["id"], site.get("domain") or "Website", "website",
            site.get("status") or "pending", url=site.get("url") or "",
            summary="%s ? %s" % (site.get("target") or "local", site.get("ssl_status") or ""),
            content=site.get("content_html") or "",
            metadata={"local_id": site["id"], "docroot": site.get("docroot") or ""},
            updated_at=site.get("updated_at") or site.get("created_at") or "",
        ))

    for automation in automations_service.list_for_org(org_id):
        items.append(_item(
            "automation", automation["id"], automation.get("name") or "Automation",
            "automation", "enabled" if automation.get("enabled") else "draft",
            summary=automation.get("description") or "",
            content={"trigger": automation.get("trigger_type") or "manual",
                     "steps": automation.get("steps") or []},
            metadata={"local_id": automation["id"],
                      "trigger_config": automation.get("trigger_config") or {}},
            updated_at=automation.get("updated_at") or automation.get("created_at") or "",
        ))
    if len(items) > MAX_RECORDS:
        items = items[:MAX_RECORDS]
    return items


def _hash(item):
    raw = json.dumps(item, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _properties(item):
    return {
        "title": item["title"], "kind": item["kind"], "local_id": str(item["local_id"]),
        "status": item["status"], "url": item["url"], "summary": item["summary"],
        "content": item["content"], "metadata": item["metadata"],
        "updated_at": item["updated_at"],
    }


def _link(org_id, object_kind, local_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM ghl_object_links WHERE org_id = ? AND object_kind = ? "
            "AND local_id = ?", (org_id, object_kind, int(local_id))
        ).fetchone()
    return {key: row[key] for key in row.keys()} if row else None


def _save_link(org_id, item, remote_id, digest, error=""):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM ghl_object_links WHERE org_id = ? AND object_kind = ? "
            "AND local_id = ?", (org_id, item["object_kind"], int(item["local_id"]))
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE ghl_object_links SET remote_record_id = ?, schema_key = ?, "
                "sync_hash = ?, synced_at = ?, error = ? WHERE id = ?",
                (remote_id, SCHEMA_KEY, digest, now_str(), error[:700], row["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO ghl_object_links (org_id, object_kind, local_id, "
                "remote_record_id, schema_key, sync_hash, synced_at, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (org_id, item["object_kind"], int(item["local_id"]), remote_id,
                 SCHEMA_KEY, digest, now_str(), error[:700]),
            )


def _create_record(org_id, item):
    body = _api(
        "POST", "/objects/%s/records" % urllib.parse.quote(SCHEMA_KEY, safe="."),
        {"locationId": _schema_location(org_id), "properties": _properties(item)},
        org_id=org_id,
    )
    record = body.get("record") or body
    remote_id = str(record.get("id") or "")
    if not remote_id:
        raise MirrorError("AscendPlatform accepted the record but returned no id.")
    return remote_id


def _update_record(org_id, item, remote_id):
    path = "/objects/%s/records/%s?%s" % (
        urllib.parse.quote(SCHEMA_KEY, safe="."),
        urllib.parse.quote(str(remote_id), safe=""),
        urllib.parse.urlencode({"locationId": _schema_location(org_id)}),
    )
    body = _api("PUT", path, {"properties": _properties(item)}, org_id=org_id)
    record = body.get("record") or body
    return str(record.get("id") or remote_id)


def sync_org(org_id, emit=None):
    """Mirror workspace records outbound. A schema/permission failure is reported, not raised."""
    if not org_id:
        raise MirrorError("Pick a client before syncing.")
    if not platform_mail.client_configured(org_id):
        raise MirrorError("Sync with AscendPlatform is not set up for this workspace yet.")

    result = {"ok": True, "total": 0, "created": 0, "updated": 0, "skipped": 0,
              "failed": [], "warnings": []}
    try:
        schema = ensure_schema(org_id)
        result["warnings"].extend(schema.get("warnings") or [])
    except Exception as exc:
        # Contacts still sync when the custom-object scope is absent. Say why the
        # workspace records did not, instead of failing the whole contact run.
        result["ok"] = False
        result["error"] = _safe_error(exc)
        return result

    items = collect_items(org_id)
    result["total"] = len(items)
    if len(items) >= MAX_RECORDS:
        result["warnings"].append(
            "Only the first %d workspace records were mirrored." % MAX_RECORDS)
    if emit:
        emit("workspace_sync", "progress",
             "Mirroring %d workspace record%s..." % (
                 len(items), "" if len(items) == 1 else "s"))

    for index, item in enumerate(items, 1):
        try:
            digest = _hash(item)
            link = _link(org_id, item["object_kind"], item["local_id"])
            if link and link.get("remote_record_id") and link.get("sync_hash") == digest:
                result["skipped"] += 1
                continue
            if link and link.get("remote_record_id"):
                try:
                    remote_id = _update_record(org_id, item, link["remote_record_id"])
                    result["updated"] += 1
                except Exception as exc:
                    if "404" not in str(exc):
                        raise
                    remote_id = _create_record(org_id, item)
                    result["created"] += 1
            else:
                remote_id = _create_record(org_id, item)
                result["created"] += 1
            _save_link(org_id, item, remote_id, digest)
        except Exception as exc:
            message = _safe_error(exc)
            result["failed"].append({
                "object": "%s #%s" % (item["object_kind"], item["local_id"]),
                "title": item["title"], "error": message,
            })
            try:
                _save_link(org_id, item, "", "", message)
            except Exception:
                pass
        if emit and (index == 1 or index % 25 == 0):
            emit("workspace_sync", "progress",
                 "Mirrored %d of %d workspace record%s..." % (
                     index, len(items), "" if len(items) == 1 else "s"))

    result["failed"] = result["failed"][:100]
    return result
