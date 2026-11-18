"""A two-way contact mirror between this workspace and AscendPlatform.

The local contact row is written first, never the other way round: a form submission
must not fail because the external CRM is down. A run pulls the remote list, merges
each contact against the last agreed snapshot, then pushes the merged result back.

Nothing in the client-facing text names the upstream provider. The provider is an
implementation detail of AscendPlatform sync.
"""

import json
import re
import urllib.parse

import contacts as contacts_service
import jobs
import platform_mail
from db import get_connection, now_str


class SyncError(ValueError):
    """A sync problem that can be shown to the operator without leaking provider names."""


# Fields that travel in both directions. ``custom_fields`` is merged separately.
SYNC_FIELDS = ("name", "email", "phone", "tags", "notes", "status", "source")

# The fields AscendPlatform owns on a remote contact. They are namespaced so they
# cannot collide with a client's own custom field called "Status" or "Notes".
MANAGED_FIELDS = (
    ("ascend_contact_id", "AscendPlatform contact id", "TEXT"),
    ("ascend_status", "AscendPlatform status", "TEXT"),
    ("ascend_source", "AscendPlatform source", "TEXT"),
    ("ascend_notes", "AscendPlatform notes", "LARGE_TEXT"),
    ("ascend_activity", "AscendPlatform activity", "LARGE_TEXT"),
    ("ascend_created_at", "AscendPlatform created", "TEXT"),
    ("ascend_updated_at", "AscendPlatform updated", "TEXT"),
)
MANAGED_KEYS = {key for key, _label, _kind in MANAGED_FIELDS}
MANAGED_LABELS = {key: label for key, label, _kind in MANAGED_FIELDS}
LABEL_TO_KEY = {label: key for key, label, _kind in MANAGED_FIELDS}

PAGE_LIMIT = 100
MAX_REMOTE = 5000


def _json(raw, fallback):
    try:
        value = json.loads(raw or "")
    except (TypeError, ValueError):
        return fallback
    return value if isinstance(value, type(fallback)) else fallback


def _safe_error(exc):
    """Never put the upstream provider's name in a client-facing result."""
    text = str(exc or "Sync failed").strip()
    text = re.sub(r"GoHighLevel|HighLevel|GHL", "AscendPlatform", text, flags=re.I)
    return text[:700]


def _now_iso():
    return now_str().replace(" ", "T") + "Z"


def state(org_id):
    """The workspace-level sync banner data."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM contact_sync_state WHERE org_id = ?", (org_id,)
        ).fetchone()
    if not row:
        return {"org_id": org_id, "last_status": "never", "last_result": {},
                "last_error": "", "last_started_at": "", "last_finished_at": ""}
    out = {key: row[key] for key in row.keys()}
    out["last_result"] = _json(out.get("last_result"), {})
    return out


def _save_state(org_id, status, result=None, error=""):
    result_json = json.dumps(result or {}, default=str)
    with get_connection() as conn:
        row = conn.execute(
            "SELECT org_id FROM contact_sync_state WHERE org_id = ?", (org_id,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE contact_sync_state SET last_finished_at = ?, last_status = ?, "
                "last_result = ?, last_error = ? WHERE org_id = ?",
                (now_str(), status, result_json, error[:700], org_id),
            )
        else:
            conn.execute(
                "INSERT INTO contact_sync_state (org_id, last_started_at, last_finished_at, "
                "last_status, last_result, last_error) VALUES (?, ?, ?, ?, ?, ?)",
                (org_id, now_str(), now_str(), status, result_json, error[:700]),
            )


def _mark_started(org_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT org_id FROM contact_sync_state WHERE org_id = ?", (org_id,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE contact_sync_state SET last_started_at = ?, last_status = 'running', "
                "last_error = '' WHERE org_id = ?", (now_str(), org_id)
            )
        else:
            conn.execute(
                "INSERT INTO contact_sync_state (org_id, last_started_at, last_status) "
                "VALUES (?, ?, 'running')", (org_id, now_str())
            )


def _field_map(catalogue):
    """Every way a GHL custom field can be referenced -> its id."""
    lookup = {}
    for field in catalogue or []:
        field_id = field.get("id") or ""
        for key in (field_id, field.get("fieldKey"), field.get("name")):
            if key:
                lookup[str(key)] = field_id
    return lookup


def _field_for_key(catalogue, key):
    lookup = _field_map(catalogue)
    return (lookup.get(key) or lookup.get("contact." + key)
            or lookup.get(MANAGED_LABELS.get(key, "")))


def _field_name_for_id(catalogue, field_id):
    for field in catalogue or []:
        if field.get("id") == field_id:
            return field.get("fieldKey") or field.get("name") or field_id
    return field_id


def _managed_id_map(catalogue):
    return {key: _field_for_key(catalogue, key) for key, _label, _kind in MANAGED_FIELDS}


def ensure_fields(org_id):
    """Make sure the namespaced AscendPlatform fields exist, without inventing client fields."""
    catalogue = platform_mail.custom_fields(org_id)
    lookup = _field_map(catalogue)
    location = platform_mail.connection(org_id)["location"]
    warnings = []
    for key, label, data_type in MANAGED_FIELDS:
        if lookup.get(key) or lookup.get("contact." + key):
            continue
        payload = {
            "name": label,
            "fieldKey": "contact." + key,
            "dataType": data_type,
            "model": "contact",
        }
        try:
            body = platform_mail.api_call(
                "POST", "/locations/%s/customFields" % location, payload, org_id=org_id
            )
            item = body.get("customField") or body.get("customField") or {}
            if not isinstance(item, dict):
                candidates = body.get("customFields")
                item = candidates[0] if isinstance(candidates, list) and candidates else {}
            field_id = item.get("id") or ""
            catalogue.append({"id": field_id, "name": label,
                              "fieldKey": "contact." + key, "dataType": data_type})
            if field_id:
                lookup[field_id] = field_id
                lookup["contact." + key] = field_id
        except Exception as exc:
            warnings.append("AscendPlatform could not create the %s field: %s"
                            % (label, _safe_error(exc)))
    return catalogue, warnings


def _tags(value):
    if isinstance(value, str):
        value = [part.strip() for part in value.split(",") if part.strip()]
    return sorted({str(tag).strip() for tag in (value or []) if str(tag).strip()})


def _normal_phone(value):
    digits = re.sub(r"[^0-9+]", "", str(value or ""))
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    return digits


def _split_name(name):
    parts = str(name or "").strip().split(None, 1)
    return (parts[0] if parts else "", parts[1] if len(parts) > 1 else "")


def _local_values(contact):
    return {
        "name": str(contact.get("name") or ""),
        "email": str(contact.get("email") or "").lower(),
        "phone": str(contact.get("phone") or ""),
        "tags": _tags(contact.get("tags")),
        "notes": str(contact.get("notes") or ""),
        "status": str(contact.get("status") or "new"),
        "source": str(contact.get("source") or "manual"),
        "custom_fields": dict(contact.get("custom_fields") or {}),
    }


def _remote_values(remote, catalogue):
    """Turn a remote contact into the same shape as a local row and its managed fields."""
    managed = {}
    custom = {}
    by_id = {}
    for field in catalogue or []:
        key = field.get("id")
        if key:
            by_id[str(key)] = field
    for item in remote.get("customFields") or []:
        field_id = str(item.get("id") or item.get("fieldId") or "")
        field = by_id.get(field_id) or {}
        raw_key = (field.get("fieldKey") or field.get("name") or "")
        key = raw_key.replace("contact.", "")
        key = LABEL_TO_KEY.get(raw_key, LABEL_TO_KEY.get(key, key))
        value = item.get("value", item.get("fieldValue", ""))
        if key in MANAGED_KEYS:
            managed[key] = value
        elif key:
            custom[key] = value
        elif field_id:
            custom[field_id] = value
    return {
        "name": str(remote.get("name") or "%s %s" % (remote.get("firstName") or "",
                                                       remote.get("lastName") or "")).strip(),
        "email": str(remote.get("email") or "").lower(),
        "phone": str(remote.get("phone") or ""),
        "tags": _tags(remote.get("tags")),
        "notes": str(managed.get("ascend_notes") or ""),
        "status": str(managed.get("ascend_status") or ""),
        "source": str(managed.get("ascend_source") or ""),
        "custom_fields": custom,
        "remote_id": str(remote.get("id") or ""),
        "remote_updated_at": str(remote.get("dateUpdated") or remote.get("updatedAt") or ""),
    }


def _activity_for(org_id, contact_id):
    """A compact, human-readable trail of form answers linked to one contact."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT fr.answers, fr.created_at, f.name, f.questions "
            "FROM form_responses fr LEFT JOIN forms f ON f.id = fr.form_id "
            "WHERE fr.org_id = ? AND fr.contact_id = ? ORDER BY fr.id DESC LIMIT 8",
            (org_id, contact_id),
        ).fetchall()
    blocks = []
    for row in rows:
        answers = _json(row["answers"], {})
        questions = _json(row["questions"], [])
        labels = {str(q.get("id")): (q.get("label") or q.get("id")) for q in questions
                  if isinstance(q, dict)}
        lines = ["Form: %s" % (row["name"] or "Untitled form")]
        if row["created_at"]:
            lines.append("When: %s" % row["created_at"])
        for key, value in answers.items():
            if isinstance(value, list):
                value = ", ".join(str(item) for item in value)
            value = str(value or "").strip()
            if value:
                lines.append("%s: %s" % (labels.get(str(key), key), value))
        blocks.append(chr(10).join(lines))
    return chr(10) + chr(10).join(blocks)


def _remote_list(org_id):
    """All contacts in the location, paginated. The caller caps the result."""
    location = platform_mail.connection(org_id)["location"]
    base = "/contacts/?" + urllib.parse.urlencode({
        "locationId": location, "limit": PAGE_LIMIT,
    })
    path = base
    out = []
    seen = set()
    while path and len(out) < MAX_REMOTE:
        body = platform_mail.api_call("GET", path, org_id=org_id)
        batch = body.get("contacts") or []
        for item in batch:
            remote_id = str(item.get("id") or "")
            if remote_id and remote_id not in seen:
                seen.add(remote_id)
                out.append(item)
        meta = body.get("meta") or {}
        next_id = str(meta.get("startAfterId") or "")
        start_after = meta.get("startAfter")
        if isinstance(start_after, list) and len(start_after) >= 2:
            next_id = next_id or str(start_after[1] or "")
            start_after = start_after[0]
        if not next_id:
            break
        path = base + "&" + urllib.parse.urlencode({
            "startAfterId": next_id,
            "startAfter": start_after or "",
        })
    return out[:MAX_REMOTE]


def _empty(value):
    return value in (None, "", [], {})


def _equal(field, left, right):
    if field == "tags":
        return _tags(left) == _tags(right)
    if field == "custom_fields":
        return dict(left or {}) == dict(right or {})
    return str(left or "") == str(right or "")


def _newer_remote(local_time, remote_time):
    if not remote_time:
        return False
    if not local_time:
        return True
    return str(remote_time).replace(" ", "T") > str(local_time).replace(" ", "T")


def _merge_first(local, remote, contact_id):
    """First link: use local values, fill blanks from remote, union tags."""
    merged = {}
    conflicts = []
    for field in SYNC_FIELDS:
        left = local.get(field)
        right = remote.get(field)
        if field == "tags":
            merged[field] = _tags(list(left or []) + list(right or []))
        elif _empty(left):
            merged[field] = right
        else:
            merged[field] = left
            if not _empty(right) and not _equal(field, left, right):
                conflicts.append({"contact_id": contact_id, "field": field,
                                  "local": left, "remote": right,
                                  "resolution": "kept AscendPlatform"})
    merged["custom_fields"] = dict(remote.get("custom_fields") or {})
    merged["custom_fields"].update(local.get("custom_fields") or {})
    return merged, conflicts


def _merge_values(local, remote, snapshot, contact_id, local_time, remote_time):
    """Three-way merge. Returns the merged values and any conflicts."""
    if not snapshot:
        return _merge_first(local, remote, contact_id)

    merged = {}
    conflicts = []
    for field in SYNC_FIELDS:
        left = local.get(field)
        right = remote.get(field)
        base = snapshot.get(field)
        left_changed = not _equal(field, left, base)
        right_changed = not _equal(field, right, base)
        # A missing remote field is not proof that somebody cleared it: the remote API
        # omits fields it does not know about. Keep the local value in that case.
        if field in ("name", "email", "phone", "notes", "status", "source"):
            if _empty(right) and not _empty(base):
                right_changed = False
        if not left_changed and not right_changed:
            merged[field] = left
        elif left_changed and not right_changed:
            merged[field] = left
        elif right_changed and not left_changed:
            merged[field] = right
        elif _equal(field, left, right):
            merged[field] = left
        else:
            # Both sides changed. Newest wins; ties go to AscendPlatform, the local
            # workspace, so a clock-less remote cannot silently overwrite the CRM.
            if _newer_remote(local_time, remote_time):
                merged[field] = right
                resolution = "newest remote value"
            else:
                merged[field] = left
                resolution = "kept AscendPlatform"
            conflicts.append({"contact_id": contact_id, "field": field,
                              "local": left, "remote": right,
                              "resolution": resolution})

    local_custom = dict(local.get("custom_fields") or {})
    remote_custom = dict(remote.get("custom_fields") or {})
    base_custom = dict(snapshot.get("custom_fields") or {})
    merged_custom = {}
    for key in sorted(set(local_custom) | set(remote_custom) | set(base_custom)):
        left = local_custom.get(key, "")
        right = remote_custom.get(key, "")
        base = base_custom.get(key, "")
        left_changed = not _equal("value", left, base)
        right_changed = not _equal("value", right, base)
        if left_changed and not right_changed:
            merged_custom[key] = left
        elif right_changed and not left_changed:
            merged_custom[key] = right
        elif _equal("value", left, right):
            merged_custom[key] = left
        else:
            merged_custom[key] = right if _newer_remote(local_time, remote_time) else left
            conflicts.append({"contact_id": contact_id, "field": "custom:" + key,
                              "local": left, "remote": right,
                              "resolution": "newest remote value" if _newer_remote(
                                  local_time, remote_time) else "kept AscendPlatform"})
    merged["custom_fields"] = merged_custom
    merged["remote_id"] = remote.get("remote_id") or ""
    merged["remote_updated_at"] = remote.get("remote_updated_at") or ""
    return merged, conflicts


def _payload(values, contact, catalogue, activity):
    first, last = _split_name(values.get("name"))
    managed_values = {
        "ascend_contact_id": str(contact["id"]),
        "ascend_status": values.get("status") or "new",
        "ascend_source": values.get("source") or "manual",
        "ascend_notes": values.get("notes") or "",
        "ascend_activity": activity or "",
        "ascend_created_at": contact.get("created_at") or "",
        "ascend_updated_at": contact.get("updated_at") or "",
    }
    ids = _managed_id_map(catalogue)
    custom = []
    for key, value in managed_values.items():
        field_id = ids.get(key)
        if field_id:
            custom.append({"id": field_id, "value": str(value)})
    for key, value in (values.get("custom_fields") or {}).items():
        if key in MANAGED_KEYS:
            continue
        field_id = _field_for_key(catalogue, key)
        if not field_id and not key.startswith("contact."):
            field_id = key
        if field_id:
            if isinstance(value, list):
                value = ", ".join(str(item) for item in value)
            entry = {"value": str(value or "")}
            if str(field_id).startswith("contact."):
                entry["key"] = field_id
            else:
                entry["id"] = field_id
            custom.append(entry)
    payload = {
        "locationId": platform_mail.connection(contact.get("org_id"))["location"],
        "name": values.get("name") or "",
        "firstName": first,
        "lastName": last,
        "tags": _tags(values.get("tags")),
        "customFields": custom,
        # The remote source is the product name, not the local lead source. The local
        # source travels in the managed field so form/manual can survive a round trip.
        "source": "AscendPlatform",
    }
    if values.get("email"):
        payload["email"] = values["email"]
    if values.get("phone"):
        payload["phone"] = values["phone"]
    return payload


def _push(org_id, contact, values, catalogue, activity):
    payload = _payload(values, contact, catalogue, activity)
    existing = contact.get("ghl_contact_id") or ""
    try:
        if existing:
            update_payload = dict(payload)
            update_payload.pop("locationId", None)
            body = platform_mail.api_call(
                "PUT", "/contacts/%s" % urllib.parse.quote(str(existing)), update_payload,
                org_id=org_id)
        else:
            body = platform_mail.api_call("POST", "/contacts/upsert", payload, org_id=org_id)
    except Exception as exc:
        if existing and "404" in str(exc):
            raise SyncError("This contact was removed from AscendPlatform; it was not "
                            "recreated automatically.")
        raise
    remote = body.get("contact") or body
    remote_id = str(remote.get("id") or existing or "")
    if not remote_id:
        raise SyncError("AscendPlatform accepted the contact but returned no id.")
    return remote_id, str(remote.get("dateUpdated") or remote.get("updatedAt") or _now_iso())


def _record_success(contact_id, org_id, remote_id, snapshot, remote_updated_at):
    contacts_service.update_contact(contact_id, org_id, {
        "name": snapshot.get("name") or "",
        "email": snapshot.get("email") or "",
        "phone": snapshot.get("phone") or "",
        "status": snapshot.get("status") or "new",
        "source": snapshot.get("source") or "manual",
        "notes": snapshot.get("notes") or "",
        "tags": snapshot.get("tags") or [],
        "custom_fields": snapshot.get("custom_fields") or {},
        "ghl_contact_id": remote_id,
        "ghl_synced_at": now_str(),
        "ghl_remote_updated_at": remote_updated_at or "",
        "ghl_error": "",
    })
    with get_connection() as conn:
        conn.execute(
            "UPDATE contacts SET ghl_snapshot = ? WHERE id = ? AND org_id = ?",
            (json.dumps(snapshot, default=str), contact_id, org_id),
        )


def _apply_local(contact, merged, org_id):
    current = _local_values(contact)
    updates = {}
    for field in SYNC_FIELDS:
        if not _equal(field, current.get(field), merged.get(field)):
            updates[field] = merged.get(field)
    if not _equal("custom_fields", current.get("custom_fields"),
                  merged.get("custom_fields")):
        updates["custom_fields"] = merged.get("custom_fields") or {}
    if updates:
        contacts_service.update_contact(contact["id"], org_id, updates)
    return updates


def _import_remote(org_id, remote, catalogue):
    if not (remote.get("email") or remote.get("phone") or remote.get("name")):
        return None, "no email, phone or name"
    status = remote.get("status") if remote.get("status") in contacts_service.STATUSES else "new"
    values = dict(remote)
    values["status"] = status
    values["source"] = "ghl"
    item = contacts_service.create_contact({
        "name": values.get("name") or "",
        "email": values.get("email") or "",
        "phone": values.get("phone") or "",
        "status": status,
        "tags": values.get("tags") or [],
        "notes": values.get("notes") or "",
        "custom_fields": values.get("custom_fields") or {},
    }, org_id, source="ghl", fire_event=False)
    _record_success(item["id"], org_id, values.get("remote_id") or "", values,
                    values.get("remote_updated_at") or _now_iso())
    return item, ""


def sync_org(org_id, emit=None):
    """Run one full two-way mirror for a workspace.

    Per-contact failures are collected; only a workspace-level problem (no credentials,
    no contact list) raises. That keeps one malformed contact from hiding the other
    successful rows from the operator.
    """
    if not org_id:
        raise SyncError("Pick a client before syncing.")
    if not platform_mail.client_configured(org_id):
        raise SyncError("Sync with AscendPlatform is not set up for this workspace yet. "
                        "Ask your agency to connect it.")

    _mark_started(org_id)
    if emit:
        emit("contact_sync", "progress", "Preparing the contact mirror...")
    result = {"ok": True, "pushed": 0, "pulled": 0, "conflicts": [],
              "failed": [], "skipped": [], "total": 0, "warnings": []}
    try:
        catalogue, warnings = ensure_fields(org_id)
        result["warnings"].extend(warnings)
        remote_rows = _remote_list(org_id)
        if len(remote_rows) >= MAX_REMOTE:
            result["warnings"].append(
                "Only the first %d remote contacts were read; the location is larger than "
                "the mirror's current page cap." % MAX_REMOTE)
        remote = [_remote_values(row, catalogue) for row in remote_rows]
        local = contacts_service.list_all_contacts(org_id)
        result["total"] = len(local) + len(remote)
        remote_by_id = {item["remote_id"]: item for item in remote if item["remote_id"]}
        remote_by_email = {item["email"]: item for item in remote if item["email"]}
        remote_by_phone = {_normal_phone(item["phone"]): item
                           for item in remote if item["phone"]}
        matched = set()

        for index, contact in enumerate(local, 1):
            if emit and (index == 1 or index % 25 == 0):
                emit("contact_sync", "progress",
                     "Merging %d of %d local contact%s..." % (
                         index, len(local), "" if len(local) == 1 else "s"))
            try:
                values = _local_values(contact)
                found = None
                if contact.get("ghl_contact_id"):
                    found = remote_by_id.get(str(contact["ghl_contact_id"]))
                if not found and values["email"]:
                    found = remote_by_email.get(values["email"])
                if not found and values["phone"]:
                    found = remote_by_phone.get(_normal_phone(values["phone"]))
                if found:
                    matched.add(found["remote_id"])
                    before = dict(values)
                    merged, conflicts = _merge_values(
                        values, found, contact.get("ghl_snapshot") or {},
                        contact["id"], contact.get("updated_at"),
                        found.get("remote_updated_at"))
                    result["conflicts"].extend(conflicts[:10])
                    if any(not _equal(field, before.get(field), merged.get(field))
                           for field in SYNC_FIELDS) or not _equal(
                               "custom_fields", before.get("custom_fields"),
                               merged.get("custom_fields")):
                        result["pulled"] += 1
                else:
                    if not contact.get("ghl_contact_id") and not (
                            values["email"] or values["phone"]):
                        result["skipped"].append({
                            "contact": contact.get("name") or contact.get("email") or
                                       "#%s" % contact["id"],
                            "reason": "needs an email or phone before it can be matched",
                        })
                        continue
                    merged = values
                activity = _activity_for(org_id, contact["id"])
                _apply_local(contact, merged, org_id)
                remote_id, remote_updated = _push(
                    org_id, contact, merged, catalogue, activity)
                _record_success(contact["id"], org_id, remote_id, merged, remote_updated)
                result["pushed"] += 1
            except Exception as exc:
                message = _safe_error(exc)
                result["failed"].append({
                    "contact": contact.get("name") or contact.get("email") or
                               "#%s" % contact["id"],
                    "error": message,
                })
                try:
                    contacts_service.update_contact(contact["id"], org_id,
                                                    {"ghl_error": message[:700]})
                except Exception:
                    pass

        if emit:
            emit("contact_sync", "progress", "Importing contacts created remotely...")
        for item in remote:
            if item["remote_id"] in matched:
                continue
            try:
                imported, problem = _import_remote(org_id, item, catalogue)
                if imported:
                    result["pulled"] += 1
                elif problem:
                    result["skipped"].append({
                        "contact": item.get("name") or item.get("email") or
                                   item["remote_id"] or "remote contact",
                        "reason": problem,
                    })
            except Exception as exc:
                result["failed"].append({
                    "contact": item.get("name") or item.get("email") or
                               item["remote_id"] or "remote contact",
                    "error": _safe_error(exc),
                })

        result["conflicts"] = result["conflicts"][:100]
        result["failed"] = result["failed"][:100]
        result["skipped"] = result["skipped"][:100]
        _save_state(org_id, "ok", result)
        if emit:
            emit("contact_sync", "result", json.dumps(result, default=str))
        return result
    except Exception as exc:
        message = _safe_error(exc)
        _save_state(org_id, "error", result, message)
        if emit:
            emit("contact_sync", "error", message)
        raise SyncError(message)


@jobs.task("contact_sync")
def contact_sync_task(job, emit):
    """Worker task: one workspace mirror, with its result stored on the job."""
    org_id = job.get("org_id")
    try:
        result = sync_org(org_id, emit=emit)
    except SyncError as exc:
        raise RuntimeError(str(exc))
    return json.dumps(result, default=str)


def start_sync(org_id, user_id=None):
    """Queue a sync and return the job id. The caller checks configuration first."""
    job_id = jobs.create_job(
        "Sync with AscendPlatform",
        "Mirror contacts and their details with AscendPlatform.",
        user_id,
        "selector",
        "contact_sync",
        org_id=org_id,
    )
    jobs.submit(job_id)
    return job_id
