"""Agent-written tools: proposed by a model, approved by a human, executed by neither yet.

Deliberately inert. `propose` writes a row with status 'pending' and nothing in the
codebase runs a row of this table - the appro val screen and the two executors (a closed
verb recipe interpreter, and a subprocess sandbox for Python) are the next piece of work,
and the gate is being built before the thing it guards.
"""

import json
import re

from db import get_connection, now_str

KINDS = ("recipe", "python")
STATUSES = ("pending", "approved", "rejected")
NAME = re.compile(r"^[a-z][a-z0-9_]{2,40}$")


class AgentToolError(ValueError):
    pass


def _out(row):
    if not row:
        return None
    out = dict(row)
    try:
        out["args_schema"] = json.loads(out.get("args_schema") or "{}")
    except (TypeError, ValueError):
        out["args_schema"] = {}
    return out


def propose(data, org_id, created_by=None, job_id=None):
    """Record a proposed tool. Returns the row; raises AgentToolError on a bad shape."""
    data = data or {}
    name = (data.get("name") or "").strip().lower()
    if not NAME.match(name):
        raise AgentToolError("Tool names are lower_snake_case, 3-40 characters: %r" % name)
    kind = (data.get("kind") or "recipe").strip().lower()
    if kind not in KINDS:
        raise AgentToolError("kind must be one of %s" % ", ".join(KINDS))
    body = data.get("body")
    if not body or not str(body).strip():
        raise AgentToolError("A tool needs a body.")
    schema = data.get("args_schema") or {}
    if isinstance(schema, str):
        try:
            schema = json.loads(schema)
        except ValueError:
            raise AgentToolError("args_schema must be JSON.")
    if kind == "recipe":
        # Recipes are declarative, so a body that is not JSON can never be executed - and
        # the verb set plus the host allow list are checked here, at proposal time, so the
        # review screen only ever shows a recipe that could actually run.
        import recipes

        problems = recipes.validate(body)
        if problems:
            raise AgentToolError(" ".join(problems))
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id, status FROM agent_tools WHERE org_id = ? AND name = ?",
            (org_id, name)).fetchone()
        if existing:
            raise AgentToolError("A tool called %s already exists (%s)." % (name,
                                                                          existing["status"]))
        cur = conn.execute(
            "INSERT INTO agent_tools (org_id, name, description, kind, args_schema, body, "
            "status, created_by, job_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
            (org_id, name, (data.get("description") or "")[:500], kind,
             json.dumps(schema), body if isinstance(body, str) else json.dumps(body),
             created_by, job_id, now_str()))
        row = conn.execute("SELECT * FROM agent_tools WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _out(row)


def list_tools(org_id, status=None, limit=100):
    query = "SELECT * FROM agent_tools WHERE org_id = ?"
    params = [org_id]
    if status:
        query += " AND status = ?"
        params.append(status)
    with get_connection() as conn:
        rows = conn.execute(query + " ORDER BY id DESC LIMIT %d" % max(1, int(limit)),
                            tuple(params)).fetchall()
    return [_out(row) for row in rows]


def get_tool(tool_id, org_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM agent_tools WHERE id = ? AND org_id = ?",
                           (tool_id, org_id)).fetchone()
    return _out(row)


def decide(tool_id, org_id, approve):
    """Approve or reject. Only a pending tool can move, so a decision is not reversible by
    a stray click and an approved tool cannot be quietly re-approved."""
    status = "approved" if approve else "rejected"
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE agent_tools SET status = ?, approved_at = ? "
            "WHERE id = ? AND org_id = ? AND status = 'pending'",
            (status, now_str() if approve else None, tool_id, org_id))
        if not cur.rowcount:
            return None
        row = conn.execute("SELECT * FROM agent_tools WHERE id = ?", (tool_id,)).fetchone()
    return _out(row)


def approved_tools(org_id):
    return list_tools(org_id, status="approved")
