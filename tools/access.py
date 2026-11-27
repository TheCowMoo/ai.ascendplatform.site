"""Who is asking, and may they change this client's public-facing setup?

Tenant ownership always comes from the job row. The role comes from the user row,
read at call time so a role change applies to the next tool call rather than to a
stale transcript. A job with no requester (an internal system task) cannot perform a
client-admin write unless the caller explicitly permits a system task and the
automation that created the job was built by a client admin or owner.
"""

import json

import user_service
from db import get_connection


def context(job_id):
    """``{org_id, requester_id, read_only, role, task}`` for the job, or empty values."""
    empty = {"org_id": None, "requester_id": None, "read_only": False,
             "role": "", "task": ""}
    if not job_id:
        return empty
    with get_connection() as conn:
        row = conn.execute(
            "SELECT org_id, requester_id, read_only, task FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if not row:
            return empty
        role = ""
        if row["requester_id"]:
            user = conn.execute(
                "SELECT role FROM users WHERE id = ?", (row["requester_id"],)
            ).fetchone()
            role = user["role"] if user else ""
    return {
        "org_id": row["org_id"],
        "requester_id": row["requester_id"],
        "read_only": bool(row["read_only"]),
        "role": user_service.normalize_role(role),
        "task": row["task"] or "",
    }


def is_manager(role):
    """True for the agency owner or a client admin; members are not managers."""
    role = user_service.normalize_role(role)
    return user_service.is_owner_role(role) or role == user_service.CLIENT_ADMIN


def _automation_creator_is_manager(job_id):
    """An event-triggered automation has no requester; use the person who built it."""
    with get_connection() as conn:
        job = conn.execute(
            "SELECT prompt, task FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if not job or (job["task"] or "") != "automation":
            return False
        try:
            payload = json.loads(job["prompt"] or "{}")
            automation_id = int(payload.get("automation_id") or 0)
        except (TypeError, ValueError):
            return False
        if not automation_id:
            return False
        automation = conn.execute(
            "SELECT created_by FROM automations WHERE id = ?", (automation_id,)
        ).fetchone()
        if not automation or not automation["created_by"]:
            return False
        creator = conn.execute(
            "SELECT role FROM users WHERE id = ?", (automation["created_by"],)
        ).fetchone()
    return bool(creator and is_manager(creator["role"]))


def manage_context(job_id, action="change this", allow_system=False):
    """``(context, error)``; error is a sentence to show instead of doing the work."""
    ctx = context(job_id)
    if ctx["org_id"] is None:
        return ctx, "Pick a client in the workspace switcher first."
    if ctx["read_only"]:
        return ctx, "This is a read-only turn, so %s is disabled." % action
    if is_manager(ctx["role"]):
        return ctx, ""
    if (allow_system and ctx["requester_id"] is None
            and _automation_creator_is_manager(job_id)):
        return ctx, ""
    return ctx, "Only a client admin can %s." % action


def can_manage(job_id):
    ctx, error = manage_context(job_id)
    return not error
