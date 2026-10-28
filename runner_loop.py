"""The isolated runner loop: executes agent-written Python and nothing else.

There is no network here (the container is started with network_mode: none), so the mailbox
is a directory: the app writes a request JSON into ``incoming/``, this loop atomically
claims it, executes, and writes a result JSON into ``outgoing/``. That is the whole
protocol, and it is the only reason the queue needs no broker.

Execution rules, in order of importance:
  * the child process gets an empty environment except RUNNER_ARGS / RUNNER_CONTEXT
  * it runs isolated (-I) with no site packages (-S)
  * a hard timeout, and the output is capped
  * a crash or a timeout is a *result*, never an exception out of this loop
"""

import json
import os
import subprocess
import sys
import time

QUEUE = os.environ.get("RUNNER_QUEUE", "/queue")
INCOMING = os.path.join(QUEUE, "incoming")
OUTGOING = os.path.join(QUEUE, "outgoing")
POLL_SECONDS = float(os.environ.get("RUNNER_POLL_SECONDS", "1"))
MAX_OUTPUT = 8000


def _write_result(name, payload):
    os.makedirs(OUTGOING, exist_ok=True)
    temp = os.path.join(OUTGOING, "." + name)
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    os.replace(temp, os.path.join(OUTGOING, name))       # atomic: the app never reads half


def _run_one(request):
    started = time.time()
    tool_id = int(request.get("tool_id") or 0)
    code = request.get("code") or ""
    timeout = max(1, min(60, int(request.get("timeout") or 20)))
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PYTHONIOENCODING": "utf-8",
        "RUNNER_ARGS": json.dumps(request.get("args") or {}),
        "RUNNER_CONTEXT": json.dumps(request.get("context") or {}),
    }
    # /tmp exists in the container; on a developer's machine it may not, and cwd must be
    # a directory that does - the child gets nothing else from the host.
    workdir = "/tmp" if os.path.isdir("/tmp") else None
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-S", "-c", code],
            capture_output=True, text=True, timeout=timeout, env=env, cwd=workdir,
        )
        ok = completed.returncode == 0
        output = (completed.stdout or "")[:MAX_OUTPUT]
        error = (completed.stderr or "")[:MAX_OUTPUT]
    except subprocess.TimeoutExpired:
        ok, output, error = False, "", "Timed out after %ss." % timeout
    except Exception as exc:                     # a broken tool must not stop the loop
        ok, output, error = False, "", "%s: %s" % (type(exc).__name__, str(exc)[:300])
    _write_result("%d.json" % tool_id, {
        "tool_id": tool_id, "ok": ok, "output": output, "error": error,
        "seconds": round(time.time() - started, 2),
    })
    return ok


def main():
    os.makedirs(INCOMING, exist_ok=True)
    os.makedirs(OUTGOING, exist_ok=True)
    sys.stderr.write("runner: watching %s\n" % INCOMING)
    while True:
        try:
            names = sorted(name for name in os.listdir(INCOMING) if name.endswith(".json"))
        except OSError:
            names = []
        for name in names:
            source = os.path.join(INCOMING, name)
            claimed = os.path.join(INCOMING, ".claimed." + name)
            try:
                os.replace(source, claimed)      # whoever renames it, owns it
            except OSError:
                continue
            try:
                with open(claimed, encoding="utf-8") as handle:
                    request = json.load(handle)
            except (OSError, ValueError) as exc:
                _write_result(name, {"tool_id": 0, "ok": False, "output": "",
                                     "error": "unreadable request: %s" % exc, "seconds": 0})
                os.remove(claimed)
                continue
            _run_one(request)
            os.remove(claimed)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
