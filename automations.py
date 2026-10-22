"""Automations: a graph of steps that runs as a job.

The graph is data, not code, so it is validated deterministically before it is stored or
executed. `validate_graph` takes no database connection and returns a list of complaints,
which is what makes it testable - the same shape as `scheduler.due_today`.

The rule that matters: an outward action (sending, publishing, provisioning) is only
allowed downstream of a `gate` node. The model may propose a graph without one, and the
validator refuses it. That is the whole safety story, in one function.
"""

import json
from datetime import datetime, timezone

NODE_TYPES = ("agent", "tool", "condition", "gate", "notify", "stop")
TRIGGER_TYPES = ("manual", "schedule", "event")
OUTWARD_TOOLS = ("send_email", "publish_site", "provision_site")
MAX_NODES = 12
MAX_NAME = 120


class AutomationError(ValueError):
    pass


def validate_graph(steps, trigger_type="manual"):
    """Everything wrong with a graph, as readable sentences. Empty list means valid."""
    problems = []
    if trigger_type not in TRIGGER_TYPES:
        problems.append("Unknown trigger '%s'." % trigger_type)
    if not isinstance(steps, list) or not steps:
        return problems + ["An automation needs at least one step."]
    if len(steps) > MAX_NODES:
        problems.append("Too many steps (%d); the limit is %d." % (len(steps), MAX_NODES))

    ids, by_id = [], {}
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            problems.append("Step %d is not an object." % (index + 1))
            continue
        node_id = str(step.get("id") or "").strip()
        if not node_id:
            problems.append("Step %d has no id." % (index + 1))
            continue
        if node_id in by_id:
            problems.append("Duplicate step id '%s'." % node_id)
            continue
        ids.append(node_id)
        by_id[node_id] = step
        kind = step.get("type")
        if kind not in NODE_TYPES:
            problems.append("Step '%s' has unknown type '%s'." % (node_id, kind))
        if kind != "stop" and not step.get("title"):
            problems.append("Step '%s' needs a title." % node_id)
        edges = step.get("next") or []
        if not isinstance(edges, list):
            problems.append("Step '%s' has malformed links." % node_id)
        elif kind in ("stop",):
            if edges:
                problems.append("Step '%s' is a stop, so it cannot link onward." % node_id)
        elif not edges:
            problems.append("Step '%s' leads nowhere - add a next step or make it a stop."
                            % node_id)

    for node_id, step in by_id.items():
        for nxt in step.get("next") or []:
            if nxt not in by_id:
                problems.append("Step '%s' links to missing step '%s'." % (node_id, nxt))

    # Entry is the first step; every other step must be reachable from it.
    if ids:
        seen, stack = set(), [ids[0]]
        while stack:
            current = stack.pop()
            if current in seen or current not in by_id:
                continue
            seen.add(current)
            stack.extend(by_id[current].get("next") or [])
        unreachable = [i for i in ids if i not in seen]
        if unreachable:
            problems.append("Unreachable from the first step: %s." % ", ".join(unreachable))

        # Cycle check, iterative, over the same edges.
        state = {}
        for start in ids:
            if state.get(start):
                continue
            stack = [(start, False)]
            while stack:
                node, leaving = stack.pop()
                if leaving:
                    state[node] = 2
                    continue
                if state.get(node) == 1:
                    problems.append("Steps loop back on themselves at '%s'." % node)
                    break
                if state.get(node) == 2:
                    continue
                state[node] = 1
                stack.append((node, True))
                for nxt in by_id.get(node, {}).get("next") or []:
                    if by_id.get(nxt) and state.get(nxt) != 2:
                        stack.append((nxt, False))
            if any("loop back" in p for p in problems):
                break

        # Outward actions require an approval gate upstream.
        gates = set()
        changed = True
        while changed:
            changed = False
            for node_id, step in by_id.items():
                if node_id in gates:
                    continue
                upstream = [n for n, s in by_id.items() if node_id in (s.get("next") or [])]
                if step.get("type") == "gate" or any(u in gates for u in upstream):
                    gates.add(node_id)
                    changed = True
        for node_id, step in by_id.items():
            tool = (step.get("config") or {}).get("tool") if isinstance(step.get("config"), dict) else None
            if step.get("type") == "tool" and tool in OUTWARD_TOOLS and node_id not in gates:
                problems.append(
                    "Step '%s' would '%s' without an approval gate before it." % (node_id, tool))

    return problems


def _clean_graph(steps, layout, trigger_type):
    problems = validate_graph(steps, trigger_type)
    if problems:
        raise AutomationError(" ".join(problems))
    clean_layout = {}
    if isinstance(layout, dict):
        for node_id, pos in layout.items():
            if node_id in {s["id"] for s in steps} and isinstance(pos, dict):
                try:
                    clean_layout[str(node_id)] = {"x": int(pos.get("x", 0)),
                                                  "y": int(pos.get("y", 0))}
                except (TypeError, ValueError):
                    continue
    return steps, clean_layout


# ---------------------------------------------------------------------------
# Storage. Every read and write is scoped by org_id - automations are a client's
# process, not platform plumbing, so one client can never see another's.
# ---------------------------------------------------------------------------
def _one(row):
    if not row:
        return None
    out = dict(row)
    for key in ("steps", "layout", "trigger_config"):
        try:
            out[key] = json.loads(out.get(key) or ("[]" if key == "steps" else "{}"))
        except (TypeError, ValueError):
            out[key] = [] if key == "steps" else {}
    return out


def create(org_id, name, steps, layout=None, trigger_type="manual",
           trigger_config=None, description="", created_by=None, enabled=False):
    from db import get_connection

    name = (name or "").strip()[:MAX_NAME]
    if not name:
        raise AutomationError("Give the automation a name.")
    steps, layout = _clean_graph(steps, layout, trigger_type)
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO automations (org_id, name, description, enabled, trigger_type, "
            "trigger_config, steps, layout, created_by) VALUES (?,?,?,?,?,?,?,?,?)",
            (org_id, name, (description or "").strip(), 1 if enabled else 0, trigger_type,
             json.dumps(trigger_config or {}), json.dumps(steps), json.dumps(layout),
             created_by),
        )
        return cur.lastrowid


def list_for_org(org_id):
    from db import get_connection

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM automations WHERE org_id = ? ORDER BY id DESC", (org_id,)
        ).fetchall()
        out = []
        for row in rows:
            item = _one(row)
            # Last run, so a card can say "parked at a gate" instead of only a timestamp.
            last = conn.execute(
                "SELECT status, error, finished_at FROM automation_runs "
                "WHERE automation_id = ? ORDER BY id DESC LIMIT 1", (item["id"],)
            ).fetchone()
            item["last_run"] = dict(last) if last else None
            out.append(item)
    return out


def get(automation_id, org_id):
    from db import get_connection

    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM automations WHERE id = ? AND org_id = ?",
            (automation_id, org_id),
        ).fetchone()
    return _one(row)


def update(automation_id, org_id, **fields):
    from db import get_connection

    current = get(automation_id, org_id)
    if not current:
        raise AutomationError("Not found")
    steps = fields.get("steps", current["steps"])
    layout = fields.get("layout", current["layout"])
    trigger_type = fields.get("trigger_type", current["trigger_type"])
    steps, layout = _clean_graph(steps, layout, trigger_type)
    name = (fields.get("name", current["name"]) or "").strip()[:MAX_NAME]
    if not name:
        raise AutomationError("Give the automation a name.")
    with get_connection() as conn:
        conn.execute(
            "UPDATE automations SET name=?, description=?, enabled=?, trigger_type=?, "
            "trigger_config=?, steps=?, layout=?, updated_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND org_id=?",
            (name,
             fields.get("description", current["description"]) or "",
             1 if fields.get("enabled", current["enabled"]) else 0,
             trigger_type,
             json.dumps(fields.get("trigger_config", current["trigger_config"])),
             json.dumps(steps), json.dumps(layout), automation_id, org_id),
        )
    return get(automation_id, org_id)


def start(automation_id, org_id, requester_id=None):
    """Queue one run. Returns the run row and the job that will walk the graph."""
    import jobs
    from db import get_connection

    automation = get(automation_id, org_id)
    if not automation:
        raise AutomationError("Not found")
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO automation_runs (automation_id, status) VALUES (?, 'queued')",
            (automation_id,),
        )
        run_id = cur.lastrowid
    job_id = jobs.create_job(
        "Automation: %s" % automation["name"],
        json.dumps({"automation_id": automation_id, "run_id": run_id}),
        requester_id, "selector", "automation", org_id,
    )
    jobs.submit(job_id)          # mandatory: a created job that is never submitted queues forever
    with get_connection() as conn:
        conn.execute("UPDATE automation_runs SET job_id = ? WHERE id = ?", (job_id, run_id))
        conn.execute("UPDATE automations SET last_run_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (automation_id,))
    return {"run_id": run_id, "job_id": job_id}


def _finish(run_id, status, error=""):
    from db import get_connection

    with get_connection() as conn:
        conn.execute(
            "UPDATE automation_runs SET status = ?, error = ?, finished_at = CURRENT_TIMESTAMP "
            "WHERE id = ?", (status, error, run_id),
        )


def _execute(step, automation, emit):
    """Do one node's work and return a short human note for the transcript.

    Agent nodes dispatch a job and move on: the automation orchestrates, it does not sit
    waiting for a write to finish. A gate stops the walk - the run parks as
    awaiting_approval rather than holding a worker for the length of an approval timeout.
    """
    import jobs

    kind = step.get("type")
    config = step.get("config") or {}
    if kind == "agent":
        child = jobs.create_job(
            "%s: %s" % (automation["name"], step.get("title") or "step"),
            config.get("prompt") or automation.get("description") or automation["name"],
            automation.get("created_by"), "selector", "agent_team", automation["org_id"],
        )
        jobs.submit(child)
        return "Started job #%d." % child
    if kind == "notify":
        return "Would report: %s" % (config.get("message") or step.get("title") or "done")
    if kind == "condition":
        return "Condition noted; branch taken on the graph's first link."
    if kind == "tool":
        return "Tool '%s' is not executed by the walker yet." % config.get("tool", "unknown")
    return "Gate." if kind == "gate" else "Nothing to do."


async def run_task(job, emit):
    """Registered as task ``automation``: walks the graph, one event per node."""
    try:
        payload = json.loads(job.get("prompt") or "{}")
    except ValueError:
        payload = {}
    automation_id = payload.get("automation_id")
    run_id = payload.get("run_id")
    automation = get(automation_id, job.get("org_id"))
    if not automation:
        raise RuntimeError("Automation %r not found" % (automation_id,))

    emit("message", "Automation", "Running “%s”." % automation["name"])
    for step in automation["steps"]:
        emit("node", "Automation", json.dumps(
            {"node": step["id"], "type": step.get("type"), "status": "running"}))
        note = _execute(step, automation, emit)
        emit("node", "Automation", json.dumps(
            {"node": step["id"], "type": step.get("type"), "status": "done", "note": note}))
        emit("message", "Automation", "%s — %s" % (step.get("title") or step["id"], note))
        if step.get("type") == "gate":
            _finish(run_id, "awaiting_approval")
            return "Parked at the approval gate “%s”." % (step.get("title") or step["id"])
    _finish(run_id, "done")
    return "Ran %d steps." % len(automation["steps"])


def delete(automation_id, org_id):
    from db import get_connection

    with get_connection() as conn:
        cur = conn.execute(
            "DELETE FROM automations WHERE id = ? AND org_id = ?", (automation_id, org_id)
        )
        return cur.rowcount > 0

