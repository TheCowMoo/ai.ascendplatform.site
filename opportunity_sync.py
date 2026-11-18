"""Mirror AscendPlatform contacts into native GHL opportunities.

The opportunity is the GHL object a salesperson already looks at. One opportunity per
contact in one pipeline is the useful mapping; form responses remain activity on the
contact rather than a second opportunity that fights GHL's duplicate rules.
"""

import hashlib
import json
import urllib.parse

import contacts as contacts_service
import platform_mail
import settings
from db import get_connection, now_str


class OpportunityError(ValueError):
    """A pipeline/opportunity problem that can be shown without provider names."""


STATUS_MAP = {"archived": "lost", "customer": "won"}


def _safe_error(exc):
    text = str(exc or "Opportunity mirror failed").strip()
    for name in ("GoHighLevel", "HighLevel", "GHL"):
        text = text.replace(name, "AscendPlatform")
    return text[:700]


def _api(method, path, payload=None, org_id=None):
    return platform_mail.api_call(method, path, payload=payload, org_id=org_id,
                                  version="v3")


def _pipeline(org_id):
    """Configured pipeline/stage, or the first usable pair in the location."""
    warnings = []
    configured = (str(settings.get("GHL_CLIENT_OPPORTUNITY_PIPELINE_ID", org_id=org_id)
                      or "").strip())
    stage = (str(settings.get("GHL_CLIENT_OPPORTUNITY_STAGE_ID", org_id=org_id)
                 or "").strip())
    if configured and stage:
        return configured, stage, warnings
    body = _api("GET", "/opportunities/pipelines?" + urllib.parse.urlencode({
        "locationId": platform_mail.connection(org_id)["location"],
    }), org_id=org_id)
    pipelines = body.get("pipelines") or []
    chosen = None
    for item in pipelines:
        if configured and str(item.get("id") or "") != configured:
            continue
        stages = item.get("stages") or []
        if stages:
            chosen = item
            break
    if chosen is None and configured:
        warnings.append("The configured opportunity pipeline has no usable stages; "
                        "the first pipeline was used instead.")
        for item in pipelines:
            if item.get("stages"):
                chosen = item
                break
    if chosen is None:
        raise OpportunityError("No opportunity pipeline with stages exists in "
                               "AscendPlatform yet.")
    stage_id = stage
    if stage_id and str(stage_id) not in [str(s.get("id")) for s in chosen["stages"]]:
        warnings.append("The configured opportunity stage is not in the selected "
                        "pipeline; the first stage was used instead.")
        stage_id = ""
    if not stage_id:
        stage_id = str(chosen["stages"][0].get("id") or "")
    return str(chosen.get("id") or ""), stage_id, warnings


def _link(org_id, contact_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM ghl_object_links WHERE org_id = ? AND object_kind = 'opportunity' "
            "AND local_id = ?", (org_id, int(contact_id))
        ).fetchone()
    return {key: row[key] for key in row.keys()} if row else None


def _save_link(org_id, contact_id, remote_id, pipeline_id, digest, error=""):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM ghl_object_links WHERE org_id = ? AND object_kind = 'opportunity' "
            "AND local_id = ?", (org_id, int(contact_id))
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE ghl_object_links SET remote_record_id = ?, schema_key = ?, "
                "sync_hash = ?, synced_at = ?, error = ? WHERE id = ?",
                (remote_id, pipeline_id, digest, now_str(), error[:700], row["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO ghl_object_links (org_id, object_kind, local_id, "
                "remote_record_id, schema_key, sync_hash, synced_at, error) "
                "VALUES (?, 'opportunity', ?, ?, ?, ?, ?, ?)",
                (org_id, int(contact_id), remote_id, pipeline_id, digest, now_str(),
                 error[:700]),
            )


def sync_org(org_id, emit=None):
    """Create/update one opportunity per contact with a remote contact id."""
    if not org_id:
        raise OpportunityError("Pick a client before syncing.")
    if not platform_mail.client_configured(org_id):
        raise OpportunityError("Sync with AscendPlatform is not set up for this workspace yet.")
    if not settings.get_bool("GHL_CLIENT_OPPORTUNITIES", True, org_id=org_id):
        return {"ok": True, "total": 0, "created": 0, "updated": 0,
                "skipped": 0, "failed": [], "warnings":
                ["Opportunity mirror is switched off."]}

    result = {"ok": True, "total": 0, "created": 0, "updated": 0,
              "skipped": 0, "failed": [], "warnings": []}
    try:
        pipeline_id, stage_id, warnings = _pipeline(org_id)
        result["warnings"].extend(warnings)
    except Exception as exc:
        result["ok"] = False
        result["error"] = _safe_error(exc)
        return result

    contacts = contacts_service.list_all_contacts(org_id)
    result["total"] = len(contacts)
    if emit:
        emit("opportunity_sync", "progress",
             "Mirroring opportunities for %d contact%s..." % (
                 len(contacts), "" if len(contacts) == 1 else "s"))
    for index, contact in enumerate(contacts, 1):
        remote_contact = str(contact.get("ghl_contact_id") or "")
        if not remote_contact:
            result["skipped"] += 1
            continue
        status = STATUS_MAP.get(str(contact.get("status") or "new"), "open")
        name = str(contact.get("name") or contact.get("email") or
                   "AscendPlatform lead")[:250]
        digest = hashlib.sha256(json.dumps({
            "name": name, "status": status, "pipeline": pipeline_id,
            "stage": stage_id, "contact": remote_contact,
        }, sort_keys=True).encode("utf-8")).hexdigest()
        link = _link(org_id, contact["id"])
        if link and link.get("remote_record_id") and link.get("sync_hash") == digest:
            result["skipped"] += 1
            continue
        payload = {
            "locationId": platform_mail.connection(org_id)["location"],
            "pipelineId": pipeline_id,
            "pipelineStageId": stage_id,
            "contactId": remote_contact,
            "name": name,
            "status": status,
            "externalObjectId": "ascend-contact-%s" % contact["id"],
        }
        if link and link.get("remote_record_id"):
            payload["id"] = link["remote_record_id"]
        try:
            body = _api("POST", "/opportunities/upsert", payload, org_id=org_id)
            opportunity = body.get("opportunity") or body
            remote_id = str(opportunity.get("id") or payload.get("id") or "")
            if not remote_id:
                raise OpportunityError("AscendPlatform accepted the opportunity but "
                                       "returned no id.")
            _save_link(org_id, contact["id"], remote_id, pipeline_id, digest)
            if payload.get("id"):
                result["updated"] += 1
            else:
                result["created"] += 1
        except Exception as exc:
            message = _safe_error(exc)
            result["failed"].append({"contact": name, "error": message})
            try:
                _save_link(org_id, contact["id"], "", pipeline_id, "", message)
            except Exception:
                pass
        if emit and (index == 1 or index % 25 == 0):
            emit("opportunity_sync", "progress",
                 "Mirrored opportunities for %d of %d contact%s..." % (
                     index, len(contacts), "" if len(contacts) == 1 else "s"))
    result["failed"] = result["failed"][:100]
    return result
