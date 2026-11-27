"""Automations: a graph of steps that runs as a job.

The graph is data, not code, so it is validated deterministically before it is stored or
executed. `validate_graph` takes no database connection and returns a list of complaints,
which is what makes it testable - the same shape as `scheduler.due_today`.

The rule that matters: an outward action (sending, publishing, provisioning) is only
allowed downstream of a `gate` node. The model may propose a graph without one, and the
validator refuses it. That is the whole safety story, in one function.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone

NODE_TYPES = ("agent", "tool", "condition", "gate", "notify", "stop", "delay")
TRIGGER_TYPES = ("manual", "schedule", "event")
EVENT_TYPES = ("contact.created", "form.submitted")
SCHEDULE_MIN_MINUTES = 5
SCHEDULE_MAX_MINUTES = 43200       # 30 days
OUTWARD_TOOLS = ("send_email", "publish_page", "publish_site", "provision_site")
MAX_NODES = 12
MAX_NAME = 120

# A condition is answered from the client's own rows, not by asking a model: a branch that
# answers differently each run is not a branch, it is a coin toss. Each fact is a count, so
# the whole language is "<count> <op> <number>".
CONDITION_FACTS = ("drafts", "sites", "failed_jobs")
CONDITION_OPS = ("==", "!=", ">", ">=", "<", "<=")


def normalize_trigger(trigger_type="manual", trigger_config=None):
    """(type, config, problems) for a trigger, in the form that gets stored.

    A draft has to be able to carry its trigger, otherwise approving it leaves the
    client with a second job: wiring the thing by hand. This is the one place that
    decides what a trigger may say.
    """
    problems = []
    kind = str(trigger_type or "manual").strip().lower()
    config = dict(trigger_config) if isinstance(trigger_config, dict) else {}
    if kind not in TRIGGER_TYPES:
        return kind, {}, ["Unknown trigger '%s'." % kind]
    clean = {}
    if kind == "event":
        event = str(config.get("event") or "").strip().lower()
        if event not in EVENT_TYPES:
            problems.append("Choose what starts it: %s." % " or ".join(EVENT_TYPES))
        clean["event"] = event
        raw_form = config.get("form_id")
        if raw_form not in (None, "", 0, "0"):
            try:
                form_id = int(raw_form)
            except (TypeError, ValueError):
                form_id = 0
            if form_id <= 0:
                problems.append("The form to watch must be a real form.")
            else:
                clean["form_id"] = form_id
    elif kind == "schedule":
        try:
            every = int(config.get("every_minutes") or 1440)
        except (TypeError, ValueError):
            every = 0
        if every < SCHEDULE_MIN_MINUTES or every > SCHEDULE_MAX_MINUTES:
            problems.append(
                "A schedule must repeat between %d minutes and 30 days."
                % SCHEDULE_MIN_MINUTES)
        else:
            clean["every_minutes"] = every
    return kind, clean, problems


def _form_trigger_problem(org_id, trigger_type, trigger_config):
    """A form-scoped trigger must name one of this client's forms."""
    if trigger_type != "event":
        return None
    if (trigger_config or {}).get("event") != "form.submitted":
        return None
    form_id = (trigger_config or {}).get("form_id")
    if not form_id:
        return None
    try:
        import forms
        if forms.get_form(int(form_id), org_id):
            return None
    except Exception:
        return None       # a missing forms table must not block an unrelated save
    return "That form isn't one of this client's."


def _form_name(org_id, form_id):
    """The form's name, or '' when it is gone. Never raises into a list response."""
    try:
        from db import get_connection
        with get_connection() as conn:
            row = conn.execute(
                "SELECT name FROM forms WHERE id = ? AND org_id = ?",
                (int(form_id), org_id)).fetchone()
        return (row["name"] if row else "").strip()
    except Exception:
        return ""


def trigger_label(trigger_type, trigger_config, org_id=None):
    """One plain sentence for the screen and the manager's explanation.

    The UI and the model should never have to say "form.submitted" or a form id.
    """
    kind = str(trigger_type or "manual").strip().lower()
    config = trigger_config if isinstance(trigger_config, dict) else {}
    if kind == "schedule":
        try:
            every = int(config.get("every_minutes") or 1440)
        except (TypeError, ValueError):
            every = 1440
        if every >= 10080 and every % 10080 == 0:
            weeks = every // 10080
            return "Every week" if weeks == 1 else "Every %d weeks" % weeks
        if every % 1440 == 0:
            days = every // 1440
            return "Every day" if days == 1 else "Every %d days" % days
        if every % 60 == 0:
            hours = every // 60
            return "Every hour" if hours == 1 else "Every %d hours" % hours
        return "Every %d minutes" % every
    if kind == "event":
        event = str(config.get("event") or "").strip().lower()
        if event == "form.submitted":
            name = _form_name(org_id, config.get("form_id")) if config.get("form_id") else ""
            return ("When someone sends “%s”" % name) if name else "When someone sends a form"
        if event == "contact.created":
            return "When a new contact is added"
        return "When something happens"
    return "Only when you press Try it now"


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


def _ungated_outward_problems(by_id, entry):
    """Every path from the entry to an outward tool must pass a gate.

    The old check asked only "is a gate somewhere upstream?". A condition with one
    branch through a gate and one branch straight to ``send_email`` satisfied that,
    and the walk then took the ungated branch - so the approval pause was skipped.
    This follows both branches and flags a tool reachable without a gate on any path.
    """
    problems = []
    flagged = set()
    seen = set()
    stack = [(entry, False)]
    while stack:
        node_id, gated = stack.pop()
        if (node_id, gated) in seen:
            continue
        seen.add((node_id, gated))
        step = by_id.get(node_id)
        if not step:
            continue
        if step.get("type") == "gate":
            gated = True
        config = step.get("config") if isinstance(step.get("config"), dict) else {}
        tool = config.get("tool")
        if (step.get("type") == "tool" and tool in OUTWARD_TOOLS
                and not gated and node_id not in flagged):
            flagged.add(node_id)
            problems.append(
                "Step '%s' would '%s' without an approval gate before it."
                % (node_id, tool))
        for nxt in step.get("next") or []:
            stack.append((nxt, gated))
    return problems


def validate_graph(steps, trigger_type="manual", trigger_config=None):
    """Everything wrong with a graph, as readable sentences. Empty list means valid."""
    problems = []
    _, _, trigger_problems = normalize_trigger(trigger_type, trigger_config)
    problems.extend(trigger_problems)
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
        elif kind == "delay":
            # Pacing for a drip sequence: "one email a day" needs a node that parks the
            # run, not a worker that sleeps. Duration is validated here so a graph cannot
            # park for a year by accident.
            minutes = 0
            config = step.get("config") or {}
            if isinstance(config, dict):
                for unit, factor in (("minutes", 1), ("hours", 60), ("days", 1440)):
                    try:
                        minutes += max(0, int(config.get(unit) or 0)) * factor
                    except (TypeError, ValueError):
                        problems.append("Step '%s' has a non-numeric %s." % (node_id, unit))
            if minutes <= 0:
                problems.append("Step '%s' waits for nothing - set days, hours or minutes."
                                % node_id)
            elif minutes > 43200:
                problems.append("Step '%s' waits longer than 30 days." % node_id)
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

        # Outward actions require an approval gate on every path to them.
        problems.extend(_ungated_outward_problems(by_id, ids[0]))

    return problems


def _clean_graph(steps, layout, trigger_type, trigger_config=None):
    problems = validate_graph(steps, trigger_type, trigger_config)
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
    out["trigger_label"] = trigger_label(
        out.get("trigger_type"), out.get("trigger_config"), out.get("org_id"))
    return out


def create(org_id, name, steps, layout=None, trigger_type="manual",
           trigger_config=None, description="", created_by=None, enabled=False):
    from db import get_connection

    name = (name or "").strip()[:MAX_NAME]
    if not name:
        raise AutomationError("Give the automation a name.")
    trigger_type, trigger_config, trigger_problems = normalize_trigger(
        trigger_type, trigger_config)
    if trigger_problems:
        raise AutomationError(" ".join(trigger_problems))
    form_problem = _form_trigger_problem(org_id, trigger_type, trigger_config)
    if form_problem:
        raise AutomationError(form_problem)
    steps, layout = _clean_graph(steps, layout, trigger_type, trigger_config)
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
    trigger_type, trigger_config, trigger_problems = normalize_trigger(
        fields.get("trigger_type", current["trigger_type"]),
        fields.get("trigger_config", current["trigger_config"]))
    if trigger_problems:
        raise AutomationError(" ".join(trigger_problems))
    form_problem = _form_trigger_problem(org_id, trigger_type, trigger_config)
    if form_problem:
        raise AutomationError(form_problem)
    steps, layout = _clean_graph(steps, layout, trigger_type, trigger_config)
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
             json.dumps(trigger_config),
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


def _delay_minutes(step):
    """Minutes to wait, from config days/hours/minutes. Validation has already run."""
    config = step.get("config") or {}
    total = 0
    for unit, factor in (("days", 1440), ("hours", 60), ("minutes", 1)):
        try:
            total += max(0, int((config or {}).get(unit) or 0)) * factor
        except (TypeError, ValueError):
            continue
    return total


def _finish(run_id, status, error="", resume_at=None, wake_at=None, context=None):
    from db import get_connection

    stopped = status in ("awaiting_approval", "waiting")
    with get_connection() as conn:
        # A parked run is not a finished run: stamping finished_at here made a waiting run
        # look done in the list, which is how "it silently did nothing" happens.
        conn.execute(
            "UPDATE automation_runs SET status = ?, error = ?, "
            "finished_at = CASE WHEN ? THEN NULL ELSE CURRENT_TIMESTAMP END WHERE id = ?",
            (status, error, 1 if stopped else 0, run_id),
        )
        if resume_at is not None:
            # Where to pick up: the gate or delay node, so resume does not re-run work.
            conn.execute("UPDATE automation_runs SET resume_at = ? WHERE id = ?",
                         (resume_at, run_id))
        if wake_at is not None:
            conn.execute("UPDATE automation_runs SET wake_at = ? WHERE id = ?",
                         (wake_at, run_id))
        if context is not None:
            conn.execute("UPDATE automation_runs SET context = ? WHERE id = ?",
                         (json.dumps(context), run_id))


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
        # Claim it. Two clicks must not start two jobs to walk the same run.
        claimed = conn.execute(
            "UPDATE automation_runs SET status = 'queued' "
            "WHERE id = ? AND status = 'awaiting_approval'", (run_id,))
        if not claimed.rowcount:
            raise AutomationError("That run is already resuming.")
    try:
        job_id = jobs.create_job(
            "Automation: %s (resumed)" % automation["name"],
            json.dumps({"automation_id": automation_id, "run_id": run_id,
                        "resume_after": gate}),
            requester_id, "selector", "automation", org_id,
        )
        jobs.submit(job_id)
    except Exception:
        # Put it back so the approval can be retried; a queued run with no job is
        # the silent failure this module exists to avoid.
        with get_connection() as conn:
            conn.execute(
                "UPDATE automation_runs SET status = 'awaiting_approval' "
                "WHERE id = ? AND status = 'queued'", (run_id,))
        raise
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
        # A submission with no identifiable answer has contact.id None. Writing the
        # string "None" into the args made a tag step look like it had a contact.
        text = text.replace("{{%s}}" % key, "" if value is None else str(value))
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
        message = str(config.get("message") or step.get("title") or "Automation finished.")
        import notifications
        result = await asyncio.to_thread(
            notifications.notify_org, automation.get("org_id"),
            "Update: %s" % (automation.get("name") or "your automation"), message)
        if result.get("ok"):
            return "Notified %s: %s" % (", ".join(result.get("sent") or []), message)
        return "Could not notify the client: %s" % (
            result.get("error") or "email is not configured")
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
                    # The job loop is shared by every run in this worker; a blocking
                    # sleep here froze them all for the runner's whole timeout.
                    await asyncio.sleep(0.5)
                return ("Queued for the runner, but nothing came back within %ss."
                        % request["timeout"])
            return "Unknown tool '%s' - nothing ran." % name
        try:
            reply = await resolved[0].run_json(args, CancellationToken())
        except Exception as exc:
            return "Tool '%s' failed: %s" % (name, str(exc)[:200])
        return str(reply)[:400]
    if kind == "delay":
        config = step.get("config") or {}
        label = []
        for unit in ("days", "hours", "minutes"):
            try:
                value = int((config or {}).get(unit) or 0)
            except (TypeError, ValueError):
                value = 0
            if value:
                label.append("%d %s" % (value, unit))
        # Parking happens in run_task; this note is what the run's transcript says when it
        # reaches the node, so it has to describe the wait, not disclaim it.
        return "Waits %s, then continues." % (", ".join(label) or "no time at all")
    if kind == "gate":
        return "Gate."


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
        if by_id[node_id].get("type") in ("stop", "gate", "delay"):
            break
    return out


import jobs


@jobs.task("automation")
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

    # A graph saved before the path-sensitive check could still carry a branch that
    # reaches an outward tool without a gate. Refuse it at run time: the editor will
    # ask for a save, which revalidates the whole graph.
    gate_problems = [problem for problem in validate_graph(
        automation["steps"], automation.get("trigger_type"),
        automation.get("trigger_config")) if "approval gate" in problem]
    if gate_problems:
        raise RuntimeError(
            "This automation would act without an approval gate on every path: %s "
            "Edit and save it again." % " ".join(gate_problems))

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
        if step.get("type") == "delay":
            minutes = _delay_minutes(step)
            wake = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime(
                "%Y-%m-%d %H:%M:%S")
            # Park instead of sleeping: a worker that sleeps through a day of waiting is a
            # worker that cannot run anything else, and it dies with the container.
            _finish(run_id, "waiting", resume_at=node_id, wake_at=wake,
                    context=payload.get("context") or {})
            return "Waiting until %s UTC before “%s”." % (wake, step.get("title") or node_id)
    _finish(run_id, "done")
    return "Ran %d steps." % len(order)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _proposal(row):
    if not row:
        return None
    out = dict(row)
    for key in ("steps", "layout", "trigger_config"):
        try:
            out[key] = json.loads(out.get(key) or ("[]" if key == "steps" else "{}"))
        except (TypeError, ValueError):
            out[key] = [] if key == "steps" else {}
    out["trigger_label"] = trigger_label(
        out.get("trigger_type"), out.get("trigger_config"), out.get("org_id"))
    return out


def propose(org_id, name, steps, automation_id=None, created_by=None, job_id=None, note="",
            trigger_type="manual", trigger_config=None):
    """Store a drafted graph and the trigger it should run on.

    Returns ``(proposal, problems)``; problems means nothing saved. Validation happens
    here so the *model* hears about a bad graph while it can still fix it, and again in
    :func:`apply_proposal` so a proposal cannot slip past a later edit.
    """
    from db import get_connection

    trigger_type, trigger_config, _ = normalize_trigger(trigger_type, trigger_config)
    problems = validate_graph(steps, trigger_type, trigger_config)
    form_problem = _form_trigger_problem(org_id, trigger_type, trigger_config)
    if form_problem:
        problems.append(form_problem)
    if problems:
        return None, problems
    clean_steps, layout = _clean_graph(steps, None, trigger_type, trigger_config)
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO automation_proposals (org_id, automation_id, name, steps, layout, "
            "trigger_type, trigger_config, status, created_by, job_id, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
            (org_id, automation_id, (name or "").strip()[:MAX_NAME], json.dumps(clean_steps),
             json.dumps(layout), trigger_type, json.dumps(trigger_config),
             created_by, job_id, (note or "")[:500], _now()),
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
    """Turn a pending proposal into a saved, enabled automation.

    Approval is the go-live decision: the trigger comes from the draft and the saved
    automation is switched on. A paused automation can be paused again in one click.
    """
    from db import get_connection

    draft = get_proposal(proposal_id, org_id)
    if not draft or draft["status"] != "pending":
        raise AutomationError("That proposal is not waiting to be applied.")
    trigger_type, trigger_config, _ = normalize_trigger(
        draft.get("trigger_type"), draft.get("trigger_config"))
    problems = validate_graph(draft["steps"], trigger_type, trigger_config)
    form_problem = _form_trigger_problem(org_id, trigger_type, trigger_config)
    if form_problem:
        problems.append(form_problem)
    if problems:
        raise AutomationError(" ".join(problems))
    if draft["automation_id"]:
        saved = update(draft["automation_id"], org_id, name=draft["name"],
                       steps=draft["steps"], layout=draft["layout"],
                       trigger_type=trigger_type, trigger_config=trigger_config,
                       enabled=True)
        if not saved:
            raise AutomationError("That automation no longer exists.")
    else:
        new_id = create(org_id, draft["name"], draft["steps"], draft["layout"],
                        trigger_type=trigger_type, trigger_config=trigger_config,
                        created_by=requester_id or draft["created_by"], enabled=True)
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
    event = str(event or "").strip().lower()
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
        wanted = str(config.get("event") or "").strip().lower()
        if wanted and wanted != event:
            continue
        # A trigger that names a form listens to that form only. Without this, every
        # "when a form is submitted" automation fired for every form on the account.
        if event == "form.submitted" and config.get("form_id"):
            submitted = str((context or {}).get("form.id") or "")
            if submitted != str(config.get("form_id")):
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
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    claimed = []
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, org_id, trigger_config, next_run_at FROM automations "
            "WHERE enabled = 1 AND trigger_type = 'schedule' "
            "AND (next_run_at IS NULL OR next_run_at = '' OR next_run_at <= ?) "
            "ORDER BY next_run_at LIMIT %d" % max(1, int(limit)), (stamp,)
        ).fetchall()
        for row in rows:
            try:
                config = json.loads(row["trigger_config"] or "{}")
            except ValueError:
                config = {}
            try:
                every = max(1, int(config.get("every_minutes") or 1440))
            except (TypeError, ValueError):
                every = 1440
            next_at = (now + _dt.timedelta(minutes=every)).strftime("%Y-%m-%d %H:%M:%S")
            due_at = row["next_run_at"]
            # Claim the turn before starting it. Two workers used to both see the
            # same due row and both start it, which doubled a scheduled email.
            if due_at in (None, ""):
                moved = conn.execute(
                    "UPDATE automations SET next_run_at = ? WHERE id = ? "
                    "AND (next_run_at IS NULL OR next_run_at = '')",
                    (next_at, row["id"]))
            else:
                moved = conn.execute(
                    "UPDATE automations SET next_run_at = ? WHERE id = ? "
                    "AND next_run_at = ?", (next_at, row["id"], due_at))
            if moved.rowcount:
                claimed.append((row["id"], row["org_id"], next_at))

    started = 0
    for automation_id, org_id, next_at in claimed:
        try:
            start(automation_id, org_id)
            started += 1
        except Exception:
            # Give the turn back rather than silently skipping a scheduled run.
            with get_connection() as conn:
                conn.execute(
                    "UPDATE automations SET next_run_at = ? WHERE id = ? "
                    "AND next_run_at = ?", (stamp, automation_id, next_at))
    return started


def delete(automation_id, org_id):
    from db import get_connection

    with get_connection() as conn:
        cur = conn.execute(
            "DELETE FROM automations WHERE id = ? AND org_id = ?", (automation_id, org_id)
        )
        return cur.rowcount > 0



def wake_due(limit=10):
    """Queue a resume job for every parked run whose delay has passed.

    The guard that matters: wake_at is cleared in the same pass that selects the row, so a
    run cannot be woken twice even if two sweeps overlap.
    """
    import jobs
    from db import get_connection

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    claimed = []
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT r.id, r.automation_id, r.resume_at, r.context, a.org_id "
            "FROM automation_runs r JOIN automations a ON a.id = r.automation_id "
            "WHERE r.status = 'waiting' AND r.wake_at IS NOT NULL AND r.wake_at <= ? "
            "ORDER BY r.wake_at LIMIT %d" % max(1, int(limit)), (now,)).fetchall()
        for row in rows:
            # Only the sweep that actually moved the row may create its job. The
            # status guard makes the loser's UPDATE affect zero rows.
            moved = conn.execute(
                "UPDATE automation_runs SET status = 'queued', wake_at = NULL "
                "WHERE id = ? AND status = 'waiting' "
                "AND wake_at IS NOT NULL AND wake_at <= ?", (row["id"], now))
            if moved.rowcount:
                claimed.append(row)
    woken = 0
    for row in claimed:
        try:
            context = json.loads(row["context"] or "{}")
        except ValueError:
            context = {}
        try:
            job_id = jobs.create_job(
                "Automation: resume after a wait",
                json.dumps({"automation_id": row["automation_id"], "run_id": row["id"],
                            "resume_after": row["resume_at"], "context": context}),
                None, "selector", "automation", org_id=row["org_id"],
            )
            jobs.submit(job_id)
            woken += 1
        except Exception:
            with get_connection() as conn:
                conn.execute(
                    "UPDATE automation_runs SET status = 'waiting', wake_at = ? "
                    "WHERE id = ? AND status = 'queued'", (now, row["id"]))
    return woken
