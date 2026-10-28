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

# A condition is answered from the client's own rows, not by asking a model: a branch that
# answers differently each run is not a branch, it is a coin toss. Each fact is a count, so
# the whole language is "<count> <op> <number>".
CONDITION_FACTS = ("drafts", "sites", "failed_jobs")
CONDITION_OPS = ("==", "!=", ">", ">=", "<", "<=")


def condition_config(step):
    """(fact, op, value, kind) from a condition node, with safe defaults."""
    config = step.get("config") or {}
    if not isinstance(config, dict):
        config = {}
    fact = str(config.get("fact") or "drafts").strip().lower()
    op = str(config.get("op") or ">").strip()
    try:
        value = int(config.get("value") or 0)
    except (TypeError, ValueError):
        value = 0
    kind = str(config.get("kind") or "").strip().lower() or None
    return fact, op, value, kind


def fact_value(org_id, fact, kind=None):
    """One number about the client. Unknown facts are 0, never an exception."""
    from db import get_connection

    if not org_id:
        return 0
    with get_connection() as conn:
        if fact == "drafts":
            sql = "SELECT COUNT(*) AS n FROM deliverables WHERE org_id = ?"
            args = [org_id]
            if kind:
                sql += " AND kind = ?"
                args.append(kind)
        elif fact == "sites":
            sql = "SELECT COUNT(*) AS n FROM sites WHERE org_id = ?"
            args = [org_id]
        elif fact == "failed_jobs":
            sql = "SELECT COUNT(*) AS n FROM jobs WHERE org_id = ? AND status = 'failed'"
            args = [org_id]
        else:
            return 0
        row = conn.execute(sql, tuple(args)).fetchone()
    return int((row["n"] if row else 0) or 0)


def evaluate_condition(step, org_id):
    """(answer, note) for a condition node.

    ``True`` takes the first link, ``False`` the second - the same edge order the editor
    draws. The note goes into the transcript, so a run explains why it went that way.
    """
    fact, op, value, kind = condition_config(step)
    if fact not in CONDITION_FACTS:
        return False, "Unknown condition '%s'; taking the second link." % fact
    if op not in CONDITION_OPS:
        return False, "Unknown comparison '%s'; taking the second link." % op
    actual = fact_value(org_id, fact, kind)
    label = "%s%s" % (fact, (" (%s)" % kind) if kind else "")
    answer = {
        "==": actual == value, "!=": actual != value,
        ">": actual > value, ">=": actual >= value,
        "<": actual < value, "<=": actual <= value,
    }[op]
    return answer, "%s is %d, %s %d is %s - took the %s link." % (
        label, actual, op, value, "true" if answer else "false",
        "first" if answer else "second",
    )


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
        elif kind == "condition":
            # First link = when true, second = when false. One link is a one-sided branch.
            if len(edges) > 2:
                problems.append(
                    "Step '%s' is a condition; it takes at most two links (then, else)."
                    % node_id)
            fact, op, _, _ = condition_config(step)
            if fact not in CONDITION_FACTS:
                problems.append(
                    "Step '%s' checks an unknown condition '%s'; use one of %s."
                    % (node_id, fact, ", ".join(CONDITION_FACTS)))
            if op not in CONDITION_OPS:
                problems.append(
                    "Step '%s' compares with an unknown operator '%s'; use one of %s."
                    % (node_id, op, " ".join(CONDITION_OPS)))

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


def start(automation_id, org_id, requester_id=None, context=None):
    """Queue one run. Returns the run row and the job that will walk the graph.

    ``context`` is what triggered the run - {"contact.id": 12} for a captured lead - and
    travels in the job payload so a step's args can use ``{{contact.id}}``.
    """
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
        json.dumps({"automation_id": automation_id, "run_id": run_id,
                    "context": context or {}}),
        requester_id, "selector", "automation", org_id,
    )
    jobs.submit(job_id)          # mandatory: a created job that is never submitted queues forever
    with get_connection() as conn:
        conn.execute("UPDATE automation_runs SET job_id = ? WHERE id = ?", (job_id, run_id))
        conn.execute("UPDATE automations SET last_run_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (automation_id,))
    return {"run_id": run_id, "job_id": job_id}


def _finish(run_id, status, error="", resume_at=None):
    from db import get_connection

    with get_connection() as conn:
        conn.execute(
            "UPDATE automation_runs SET status = ?, error = ?, finished_at = CURRENT_TIMESTAMP "
            "WHERE id = ?", (status, error, run_id),
        )
        if resume_at is not None:
            # Where to pick up: the gate node, so resume does not re-run earlier work.
            conn.execute("UPDATE automation_runs SET resume_at = ? WHERE id = ?",
                         (resume_at, run_id))


def resume(automation_id, org_id, requester_id=None):
    """Continue the newest run that is parked at a gate. Returns the new job id."""
    import jobs
    from db import get_connection

    automation = get(automation_id, org_id)
    if not automation:
        raise AutomationError("Not found")
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, resume_at FROM automation_runs WHERE automation_id = ? "
            "AND status = 'awaiting_approval' ORDER BY id DESC LIMIT 1", (automation_id,)
        ).fetchone()
        if not row:
            raise AutomationError("Nothing is waiting for approval.")
        run_id, gate = row["id"], row["resume_at"]
        conn.execute("UPDATE automation_runs SET status = 'queued' WHERE id = ?", (run_id,))
    job_id = jobs.create_job(
        "Automation: %s (resumed)" % automation["name"],
        json.dumps({"automation_id": automation_id, "run_id": run_id,
                    "resume_after": gate}),
        requester_id, "selector", "automation", org_id,
    )
    jobs.submit(job_id)
    with get_connection() as conn:
        conn.execute("UPDATE automation_runs SET job_id = ? WHERE id = ?", (job_id, run_id))
    return {"run_id": run_id, "job_id": job_id}


def _fill_args(args, context):
    """Substitute {{contact.id}}-style keys in a step's args from the triggering event.

    Substitution happens on the JSON text rather than on the parsed values, so a whole arg
    can be a placeholder ({"contact_id": "{{contact.id}}"}) without caring whether the
    value is a string or a number.
    """
    if not context or not args:
        return args
    text = json.dumps(args)
    for key, value in (context or {}).items():
        text = text.replace("{{%s}}" % key, str(value))
    try:
        return json.loads(text)
    except ValueError:
        return args


async def _execute(step, automation, emit, job_id=None, context=None):
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
        _, note = evaluate_condition(step, automation.get("org_id"))
        return note
    if kind == "tool":
        # A tool node runs through the same registry the agents use, so the approval gate
        # and the policy checks that guard a chat-driven send guard this too - the
        # validator already refuses an outward tool without a gate upstream.
        name = (config.get("tool") or "").strip()
        if not name:
            return "This step names no tool, so nothing ran."
        args = _fill_args(config.get('args') or {}, context)
        if not isinstance(args, dict):
            return "This step's args must be an object."
        import tools as tool_registry
        from autogen_core import CancellationToken

        resolved = tool_registry.resolve([name], job_id=job_id)
        if not resolved:
            # Not a built-in: an approved agent-written recipe is the other possibility.
            # Only 'approved' rows are considered, so a pending proposal cannot run.
            import agent_tools
            import recipes
            from db import get_connection

            org_id = None
            if job_id:
                with get_connection() as conn:
                    row = conn.execute("SELECT org_id FROM jobs WHERE id = ?",
                                       (job_id,)).fetchone()
                org_id = row["org_id"] if row else None
            match = next((tool for tool in agent_tools.approved_tools(org_id)
                          if tool["name"] == name), None) if org_id else None
            if match and match["kind"] == "recipe":
                values = dict(context or {})
                values["org_id"] = org_id
                results = recipes.run(match["body"], args, values)
                ok = all(entry[1] for entry in results)
                detail = "; ".join("%s: %s" % (entry[0], entry[2]) for entry in results)
                return ("%s %s" % ("recipe" if ok else "recipe failed -", detail))[:400]

            if match and match["kind"] == "python":
                # Written to a directory the isolated runner watches. This process never
                # executes the code - that is the whole point of the container.
                import json as _json
                import os
                import time

                import runner_protocol

                values = dict(context or {})
                values["org_id"] = org_id
                try:
                    request = runner_protocol.build_request(match, args, values)
                except runner_protocol.RunnerError as exc:
                    return "Not run: %s" % exc
                queue = os.environ.get("RUNNER_QUEUE_DIR") or "/srv/runner-queue"
                incoming = os.path.join(queue, "incoming")
                result_path = os.path.join(queue, "outgoing", "%d.json" % request["tool_id"])
                os.makedirs(incoming, exist_ok=True)
                stamp = "%d-%d.json" % (request["tool_id"], int(time.time() * 1000))
                with open(os.path.join(incoming, stamp), "w", encoding="utf-8") as handle:
                    _json.dump(request, handle)
                deadline = time.time() + request["timeout"] + 15
                while time.time() < deadline:
                    if os.path.exists(result_path):
                        try:
                            with open(result_path, encoding="utf-8") as handle:
                                payload = _json.load(handle)
                            os.remove(result_path)
                        except (OSError, ValueError):
                            break
                        result = runner_protocol.parse_result(payload)
                        runner_protocol.record_result(match["id"], org_id, result)
                        return runner_protocol.summarize(result)
                    time.sleep(0.5)
                return ("Queued for the runner, but nothing came back within %ss."
                        % request["timeout"])
            return "Unknown tool '%s' - nothing ran." % name
        try:
            reply = await resolved[0].run_json(args, CancellationToken())
        except Exception as exc:
            return "Tool '%s' failed: %s" % (name, str(exc)[:200])
        return str(reply)[:400]
    return "Gate." if kind == "gate" else "Nothing to do."


def _chain(steps, org_id=None):
    """Every node reachable by following edges, gates included. No side effects.

    A condition picks which edge: the first when it is true, the second when it is false.
    Everything else follows its first link, which is what the editor draws.
    """
    if not steps:
        return []
    by_id = {s["id"]: s for s in steps}
    order, seen, current = [], set(), steps[0]["id"]
    while current and current in by_id and current not in seen:
        seen.add(current)
        order.append(current)
        step = by_id[current]
        if step.get("type") == "stop":
            break
        links = step.get("next") or []
        if step.get("type") == "condition":
            answer, _ = evaluate_condition(step, org_id)
            if answer:
                current = links[0] if links else None
            else:
                current = links[1] if len(links) > 1 else None
        else:
            current = links[0] if links else None
    return order


def walk(steps, resume_after=None, org_id=None):
    """The nodes this job runs, in order, stopping at the next gate.

    ``resume_after`` is the gate a parked run stopped at: the walk picks up at the node
    its edge points to, so a resumed job never re-runs the work before the approval.
    ``org_id`` answers the conditions on the way - without it every condition reads 0.
    """
    order = _chain(steps, org_id)
    if resume_after in order:
        order = order[order.index(resume_after) + 1:]
    by_id = {s["id"]: s for s in steps}
    out = []
    for node_id in order:
        out.append(node_id)
        if by_id[node_id].get("type") in ("stop", "gate"):
            break
    return out


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

    steps = {s["id"]: s for s in automation["steps"]}
    emit("message", "Automation", "Running “%s”." % automation["name"])
    order = walk(automation["steps"], payload.get("resume_after"), automation.get("org_id"))
    for node_id in order:
        step = steps[node_id]
        emit("node", "Automation", json.dumps(
            {"node": node_id, "type": step.get("type"), "status": "running"}))
        note = await _execute(step, automation, emit, job.get("id"), payload.get("context"))
        emit("node", "Automation", json.dumps(
            {"node": node_id, "type": step.get("type"), "status": "done", "note": note}))
        emit("message", "Automation", "%s — %s" % (step.get("title") or node_id, note))
        if step.get("type") == "gate":
            _finish(run_id, "awaiting_approval", resume_at=node_id)
            return "Parked at the approval gate “%s”." % (step.get("title") or node_id)
    _finish(run_id, "done")
    return "Ran %d steps." % len(order)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _proposal(row):
    if not row:
        return None
    out = dict(row)
    for key in ("steps", "layout"):
        try:
            out[key] = json.loads(out.get(key) or ("[]" if key == "steps" else "{}"))
        except (TypeError, ValueError):
            out[key] = [] if key == "steps" else {}
    return out


def propose(org_id, name, steps, automation_id=None, created_by=None, job_id=None, note=""):
    """Store a drafted graph. Returns (proposal, problems); problems means nothing saved.

    Validation happens here so the *model* hears about a bad graph while it can still fix
    it, and again in :func:`apply` so a proposal cannot slip past a later edit.
    """
    from db import get_connection

    problems = validate_graph(steps)
    if problems:
        return None, problems
    clean_steps, layout = _clean_graph(steps, None, "manual")
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO automation_proposals (org_id, automation_id, name, steps, layout, "
            "status, created_by, job_id, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
            (org_id, automation_id, (name or "").strip()[:MAX_NAME], json.dumps(clean_steps),
             json.dumps(layout), created_by, job_id, (note or "")[:500], _now()),
        )
        row = conn.execute("SELECT * FROM automation_proposals WHERE id = ?",
                           (cur.lastrowid,)).fetchone()
    return _proposal(row), []


def list_proposals(org_id, status="pending"):
    from db import get_connection

    query = "SELECT * FROM automation_proposals WHERE org_id = ?"
    params = [org_id]
    if status:
        query += " AND status = ?"
        params.append(status)
    with get_connection() as conn:
        rows = conn.execute(query + " ORDER BY id DESC", tuple(params)).fetchall()
    return [_proposal(row) for row in rows]


def get_proposal(proposal_id, org_id):
    from db import get_connection

    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM automation_proposals WHERE id = ? AND org_id = ?",
            (proposal_id, org_id)).fetchone()
    return _proposal(row)


def apply_proposal(proposal_id, org_id, requester_id=None):
    """Turn a pending proposal into a saved automation. (automation, problems)."""
    from db import get_connection

    draft = get_proposal(proposal_id, org_id)
    if not draft or draft["status"] != "pending":
        raise AutomationError("That proposal is not waiting to be applied.")
    problems = validate_graph(draft["steps"])
    if problems:
        raise AutomationError(" ".join(problems))
    if draft["automation_id"]:
        saved = update(draft["automation_id"], org_id, name=draft["name"],
                       steps=draft["steps"], layout=draft["layout"])
        if not saved:
            raise AutomationError("That automation no longer exists.")
    else:
        new_id = create(org_id, draft["name"], draft["steps"], draft["layout"],
                        created_by=requester_id or draft["created_by"])
        saved = get(new_id, org_id)
    with get_connection() as conn:
        conn.execute("UPDATE automation_proposals SET status = 'applied' WHERE id = ?",
                     (proposal_id,))
    return saved, []


def discard_proposal(proposal_id, org_id):
    from db import get_connection

    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE automation_proposals SET status = 'discarded' "
            "WHERE id = ? AND org_id = ? AND status = 'pending'", (proposal_id, org_id))
        return cur.rowcount > 0


def start_for_event(org_id, event, requester_id=None, context=None):
    """Start every enabled automation whose trigger is this event.

    This is the wire between "somebody signed up" and "the sequence begins". Failures are
    contained: an automation that cannot start must never break the thing that caused it,
    because the cause is a real person's form submission.
    """
    started = []
    try:
        rows = list_for_org(org_id)
    except Exception:
        return started
    for automation in rows:
        if not automation.get("enabled"):
            continue
        if (automation.get("trigger_type") or "manual") != "event":
            continue
        config = automation.get("trigger_config") or {}
        wanted = (config.get("event") or "").strip()
        if wanted and wanted != event:
            continue
        try:
            result = start(automation["id"], org_id, requester_id, context)
            started.append({"automation": automation["id"], "run": result.get("run_id"),
                            "job": result.get("job_id")})
        except Exception:
            continue
    return started


def run_due(limit=20):
    """Start automations whose schedule has come round. Returns how many started.

    The editor offered "on a schedule" before anything swept for due runs, which is the
    same broken promise as a tool step that refused to execute: the UI said yes and the
    backend never looked. ``next_run_at`` already existed on the row; this is what writes
    it and what reads it.
    """
    import datetime as _dt

    from db import get_connection

    now = _dt.datetime.now(_dt.timezone.utc)
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, org_id, trigger_config, next_run_at FROM automations "
            "WHERE enabled = 1 AND trigger_type = 'schedule' LIMIT %d" % max(1, int(limit))
        ).fetchall()

    started = 0
    for row in rows:
        due_at = row["next_run_at"]
        if due_at:
            try:
                when = _dt.datetime.strptime(str(due_at)[:19], "%Y-%m-%d %H:%M:%S")
                when = when.replace(tzinfo=_dt.timezone.utc)
            except ValueError:
                when = None
            if when and when > now:
                continue
        try:
            config = json.loads(row["trigger_config"] or "{}")
        except ValueError:
            config = {}
        try:
            every = max(1, int(config.get("every_minutes") or 1440))
        except (TypeError, ValueError):
            every = 1440
        try:
            start(row["id"], row["org_id"])
            started += 1
        except Exception:
            continue          # one broken automation must not stop the others
        with get_connection() as conn:
            conn.execute("UPDATE automations SET next_run_at = ? WHERE id = ?",
                         ((now + _dt.timedelta(minutes=every)).strftime("%Y-%m-%d %H:%M:%S"),
                          row["id"]))
    return started


def delete(automation_id, org_id):
    from db import get_connection

    with get_connection() as conn:
        cur = conn.execute(
            "DELETE FROM automations WHERE id = ? AND org_id = ?", (automation_id, org_id)
        )
        return cur.rowcount > 0

