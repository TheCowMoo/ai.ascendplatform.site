"""The runner contract: what crosses the boundary to the isolated executor.

Agent-written Python is never executed in the app process. It is sent to a `runner`
service that has no network, a read-only filesystem, its own user, and memory/CPU/pid
limits (see docker-compose.yml). This module is the *only* thing both sides share, so the
trust boundary is a small, testable set of dictionaries rather than a function call.

Contract, in full:

    request   {"tool_id": int, "org_id": int, "code": str,
               "args": {...}, "context": {...}, "timeout": int}
    response  {"tool_id": int, "ok": bool, "output": str, "error": str,
               "seconds": float}

Everything here is pure: no Redis, no subprocess, no imports of the app. That is what makes
it testable before there is anything dangerous to test.
"""

MAX_CODE = 20000
MAX_OUTPUT = 8000
DEFAULT_TIMEOUT = 20
MAX_TIMEOUT = 60


class RunnerError(ValueError):
    pass


def build_request(agent_tool, args=None, context=None, timeout=DEFAULT_TIMEOUT):
    """A request for the isolated runner. Refuses anything that is not a python tool."""
    if not agent_tool:
        raise RunnerError("No tool to run.")
    if (agent_tool.get("kind") or "") != "python":
        raise RunnerError("Only python tools go to the runner; recipes are interpreted here.")
    if (agent_tool.get("status") or "") != "approved":
        raise RunnerError("Tool %s is %s, not approved." % (agent_tool.get("id"),
                                                            agent_tool.get("status")))
    code = agent_tool.get("body") or ""
    if not str(code).strip():
        raise RunnerError("The tool has no code.")
    if len(str(code)) > MAX_CODE:
        raise RunnerError("The code is %d characters; the limit is %d."
                          % (len(str(code)), MAX_CODE))
    try:
        timeout = int(timeout or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    return {
        "tool_id": int(agent_tool.get("id") or 0),
        "org_id": int(agent_tool.get("org_id") or 0),
        "code": str(code),
        "args": dict(args or {}),
        "context": dict(context or {}),
        "timeout": max(1, min(MAX_TIMEOUT, timeout)),
    }


def parse_result(payload):
    """(tool_id, ok, output, error, seconds) from whatever the runner sent back.

    Anything malformed is treated as a failure rather than an omission: a runner that
    returns nothing must not look like a tool that did nothing.
    """
    if not isinstance(payload, dict):
        raise RunnerError("The runner sent %r, not an object." % type(payload).__name__)
    try:
        tool_id = int(payload.get("tool_id") or 0)
    except (TypeError, ValueError):
        raise RunnerError("The result has no usable tool_id.")
    ok = bool(payload.get("ok"))
    output = str(payload.get("output") or "")[:MAX_OUTPUT]
    error = str(payload.get("error") or "")[:MAX_OUTPUT]
    try:
        seconds = round(float(payload.get("seconds") or 0), 2)
    except (TypeError, ValueError):
        seconds = 0.0
    if not ok and not error:
        error = "The tool failed without saying why."
    return tool_id, ok, output, error, seconds


def summarize(result):
    """One line for the job transcript, short enough to read."""
    _, ok, output, error, seconds = result
    if ok:
        return ("ran in %ss: %s" % (seconds, output or "no output"))[:600]
    return ("failed in %ss: %s" % (seconds, error))[:600]


def record_result(agent_tool_id, org_id, result):
    """Store the outcome against the tool row, so a broken tool is visible as broken.

    Only updates the row it belongs to and only for that client - a runner reply cannot
    touch anyone else's tool even if it claims to.
    """
    from db import get_connection

    tool_id, ok, output, error, _ = result
    if int(tool_id) != int(agent_tool_id):
        raise RunnerError("The result is for tool %s, not %s." % (tool_id, agent_tool_id))
    with get_connection() as conn:
        conn.execute(
            "UPDATE agent_tools SET last_error = ? WHERE id = ? AND org_id = ?",
            ("" if ok else (error or output)[:500], int(agent_tool_id), int(org_id)))
    return ok
