"""
Action gate: the single door every side-effecting tool goes through.

Two modes:

* **dry-run** (the default) — the action is described and recorded in the job
  transcript, but never performed. Nothing can escape a test environment.
* **live** — enabled with ``ALLOW_LIVE_ACTIONS=1``, or from the Settings tab.
  A write/exec action is recorded as an approval request and the tool blocks
  until a human approves or denies it in the UI, or the approval times out.

Because both modes share this path, the same code is exercised in tests as in
production — only the outcome differs.
"""

import json
import time

import settings
from db import get_connection, now_str

APPROVAL_POLL = 0.5


def is_dry_run():
    """True until live actions are enabled, in Settings or via ALLOW_LIVE_ACTIONS.

    Read per call rather than cached at import, so flipping the switch in the
    Settings tab applies to the next action without restarting the app.
    """
    return not settings.get_bool("ALLOW_LIVE_ACTIONS", False)


def approval_timeout():
    """Seconds to wait for an operator decision before aborting the action."""
    return settings.get_int("APPROVAL_TIMEOUT", 300)


def mode():
    return "dry-run" if is_dry_run() else "live"


def _as_dict(row):
    return {k: row[k] for k in row.keys()}


# ---------------------------------------------------------------------------
# Approval records
# ---------------------------------------------------------------------------
def request_approval(tool, summary, args=None, job_id=None):
    """Record a pending approval and return its id."""
    payload = json.dumps(args or {}, default=str)
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO action_approvals (job_id, tool, summary, args_json, status) "
            "VALUES (?, ?, ?, ?, 'pending')",
            (job_id, tool, summary, payload),
        )
        return cur.lastrowid


def get_approval(approval_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM action_approvals WHERE id = ?", (approval_id,)
        ).fetchone()
    return _as_dict(row) if row else None


def list_approvals(status=None, limit=50, org_id=None):
    """Pending/decided writes, newest first.

    ``org_id`` scopes them through the job that asked for the write. An approval
    with no job (raised outside a run) belongs to no client, so only the platform
    operator can see it - which is why the filter is on the join, not the row.
    """
    query = ("SELECT a.* FROM action_approvals a "
             "LEFT JOIN jobs j ON j.id = a.job_id WHERE 1 = 1")
    params = []
    if status:
        query += " AND a.status = ?"
        params.append(status)
    if org_id is not None:
        query += " AND j.org_id = ?"
        params.append(org_id)
    query += " ORDER BY a.id DESC LIMIT ?"
    params.append(limit)
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_as_dict(r) for r in rows]


def approval_org_id(approval_id):
    """The org that owns an approval's job, or None if it has no job."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT j.org_id AS org_id FROM action_approvals a "
            "LEFT JOIN jobs j ON j.id = a.job_id WHERE a.id = ?",
            (approval_id,),
        ).fetchone()
    return row["org_id"] if row else None


def decide_approval(approval_id, decision, decided_by="", result=""):
    """Approve or deny a pending request. Returns the updated row."""
    if decision not in ("approved", "denied"):
        raise ValueError("decision must be 'approved' or 'denied'")
    with get_connection() as conn:
        conn.execute(
            "UPDATE action_approvals SET status = ?, decided_at = ?, decided_by = ?, result = ? "
            "WHERE id = ? AND status = 'pending'",
            (decision, now_str(), decided_by or "", result or "", approval_id),
        )
    return get_approval(approval_id)


def set_result(approval_id, result):
    with get_connection() as conn:
        conn.execute(
            "UPDATE action_approvals SET result = ? WHERE id = ?", (str(result), approval_id)
        )


def wait_for_approval(approval_id, timeout=None):
    """Block until the request is decided or the timeout expires."""
    limit = timeout if timeout is not None else approval_timeout()
    deadline = time.time() + limit
    while time.time() < deadline:
        row = get_approval(approval_id)
        if row is None:
            return None
        if row["status"] != "pending":
            return row
        time.sleep(APPROVAL_POLL)
    return get_approval(approval_id)


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------
def gate(tool, summary, args=None, job_id=None, emit=None):
    """Authorise a side-effecting action.

    Returns a dict with ``allowed``, ``dry_run``, ``reason`` and ``approval_id``.
    In dry-run the action is never performed, so it is "allowed" as a simulation.
    """
    def note(message):
        if emit is not None:
            emit("gate", "system", message)

    if is_dry_run():
        note("[dry-run] %s: %s (not executed - set ALLOW_LIVE_ACTIONS=1 to enable)" % (tool, summary))
        return {"allowed": True, "dry_run": True, "reason": "dry-run", "approval_id": None}

    approval_id = request_approval(tool, summary, args, job_id)
    note("Awaiting approval #%d: %s" % (approval_id, summary))

    row = wait_for_approval(approval_id)
    if row is None:
        return {"allowed": False, "dry_run": False, "reason": "approval record vanished", "approval_id": None}
    if row["status"] == "approved":
        note("Approved #%d by %s" % (approval_id, row["decided_by"] or "operator"))
        return {"allowed": True, "dry_run": False, "reason": "approved", "approval_id": approval_id}
    if row["status"] == "denied":
        note("Denied #%d by %s" % (approval_id, row["decided_by"] or "operator"))
        return {"allowed": False, "dry_run": False, "reason": "denied by operator", "approval_id": approval_id}

    note("Approval #%d timed out after %ds; action aborted" % (approval_id, approval_timeout()))
    return {"allowed": False, "dry_run": False, "reason": "approval timed out", "approval_id": approval_id}
