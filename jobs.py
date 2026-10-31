"""
Background job runner for agent runs.

AutoGen is async; Flask is sync WSGI. Two execution backends drive the same task
functions:

* ``local`` (default) — a daemon thread owning an asyncio loop and a queue.
  Good for development and single-container installs.
* ``redis`` — enqueue onto an arq worker so runs survive app restarts and scale
  across containers. Set ``JOB_BACKEND=redis`` and ``REDIS_URL=redis://...``,
  then start ``python -m worker``.

Jobs and their event logs are persisted to the database, so the admin UI can poll
them (or stream them over SSE) and the transcript survives a reload.

Task functions are registered by name via :func:`task`. ``agents.py`` registers
the AutoGen team runner (``agent_team``).
"""

import asyncio
import json
import os
import queue
import threading
import traceback

from db import get_connection, now_str

DEFAULT_TASK = "echo"
TERMINAL_STATUSES = ("done", "failed", "cancelled")
REDIS_URL = (os.environ.get("REDIS_URL") or "").strip()
JOB_BACKEND = (os.environ.get("JOB_BACKEND") or "local").strip().lower()

# Registered job tasks: name -> callable(job, emit), sync or async.
TASKS = {}

_runner = None
_runner_lock = threading.Lock()


def task(name):
    """Register a job task function under ``name``.

    Signature: ``fn(job, emit)``, may be sync or async. ``emit(source, type,
    content)`` appends an entry to the job's event log. Return a short result
    string (or None).
    """

    def register(fn):
        TASKS[name] = fn
        return fn

    return register


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------
def _as_dict(row):
    return {k: row[k] for k in row.keys()}


def create_job(title, prompt, requester_id=None, team_mode="selector",
               task_name=DEFAULT_TASK, org_id=None):
    """Create a job. ``org_id`` is the tenant that owns it, and scopes who can see it."""
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO jobs (title, prompt, requester_id, team_mode, task, org_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (title or "", prompt or "", requester_id, team_mode, task_name, org_id),
        )
        return cur.lastrowid


def get_job(job_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _as_dict(row) if row else None


def list_jobs(limit=50, org_id=None):
    """Recent jobs, newest first. ``org_id`` scopes them to one client's runs.

    ``org_id=None`` is the platform operator's view. Note the ``is not None`` check:
    0 is the "no access" marker and must filter, not fall through to "no filter".
    """
    query = "SELECT * FROM jobs"
    params = []
    if org_id is not None:
        query += " WHERE org_id = ?"
        params.append(org_id)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_as_dict(r) for r in rows]


def get_events(job_id, after_seq=0, limit=500):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM job_events WHERE job_id = ? AND seq > ? ORDER BY seq ASC LIMIT ?",
            (job_id, after_seq, limit),
        ).fetchall()
    return [_as_dict(r) for r in rows]


def add_event(job_id, source, type_, content):
    """Append an event to a job's transcript. Returns the assigned sequence."""
    if isinstance(content, (dict, list)):
        content = json.dumps(content, indent=2, default=str)
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS m FROM job_events WHERE job_id = ?", (job_id,)
        ).fetchone()
        seq = (row["m"] or 0) + 1
        conn.execute(
            "INSERT INTO job_events (job_id, seq, source, type, content) VALUES (?, ?, ?, ?, ?)",
            (job_id, seq, source or "", type_ or "message", str(content)),
        )
    return seq


def add_artifact(job_id, kind, path, mime=""):
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO job_artifacts (job_id, kind, path, mime) VALUES (?, ?, ?, ?)",
            (job_id, kind or "file", path or "", mime or ""),
        )
        return cur.lastrowid


def list_artifacts(job_id):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM job_artifacts WHERE job_id = ? ORDER BY id ASC", (job_id,)
        ).fetchall()
    return [_as_dict(r) for r in rows]


def _mark_running(job_id):
    with get_connection() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'running', started_at = ?, error = '' WHERE id = ?",
            (now_str(), job_id),
        )


def _mark_finished(job_id, status, result="", error=""):
    with get_connection() as conn:
        conn.execute(
            "UPDATE jobs SET status = ?, result = ?, error = ?, finished_at = ? WHERE id = ?",
            (status, result or "", error or "", now_str(), job_id),
        )


def cancel_job(job_id):
    """Flag a job as cancelled. Running tasks should check this periodically."""
    with get_connection() as conn:
        row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return False
        if row["status"] in ("queued", "running"):
            conn.execute(
                "UPDATE jobs SET status = 'cancelled', finished_at = ? WHERE id = ?",
                (now_str(), job_id),
            )
    return True


def is_cancelled(job_id):
    job = get_job(job_id)
    return bool(job and job["status"] == "cancelled")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
class JobRunner:
    """Daemon thread + asyncio loop that executes queued jobs.

    Jobs are submitted from Flask worker threads and marshalled onto the runner
    loop with ``call_soon_threadsafe`` — no blocking executor threads, so the
    process can still exit cleanly.
    """

    def __init__(self):
        self._pending = queue.Queue()   # submitted before the loop is ready
        self._queue = None
        self._loop = None
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._thread_main, name="job-runner", daemon=True)
        self._thread.start()

    def submit(self, job_id):
        loop = self._loop
        if loop is None:
            self._pending.put(job_id)
            return
        loop.call_soon_threadsafe(self._queue.put_nowait, job_id)

    def _thread_main(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._queue = asyncio.Queue()
        self._loop = loop
        try:
            # Hand over anything submitted before the loop was ready.
            while True:
                try:
                    loop.call_soon_threadsafe(self._queue.put_nowait, self._pending.get_nowait())
                except queue.Empty:
                    break
            loop.run_until_complete(self._main())
        except Exception:
            traceback.print_exc()
        finally:
            self._loop = None

    async def _main(self):
        while True:
            job_id = await self._queue.get()
            if job_id is None:
                return
            try:
                await run_job(job_id)
            except Exception:
                traceback.print_exc()


# ---------------------------------------------------------------------------
# Execution (shared by the in-process runner and the arq worker)
# ---------------------------------------------------------------------------
async def run_job(job_id):
    """Execute one job by id. Safe to call from any asyncio loop."""
    job = get_job(job_id)
    if not job or job["status"] == "cancelled":
        return None

    name = job.get("task") or DEFAULT_TASK
    fn = TASKS.get(name)
    if fn is None:
        add_event(job_id, "system", "error", "Unknown task: %s" % name)
        _mark_finished(job_id, "failed", error="Unknown task: %s" % name)
        return None

    _mark_running(job_id)
    add_event(job_id, "system", "system", "Job started (task=%s, mode=%s)" % (name, job.get("team_mode")))

    def emit(source, type_, content):
        add_event(job_id, source, type_, content)

    try:
        output = fn(job, emit)
        if asyncio.iscoroutine(output):
            output = await output
        if is_cancelled(job_id):
            add_event(job_id, "system", "system", "Job cancelled")
            return None
        _mark_finished(job_id, "done", result=str(output or "done"))
        add_event(job_id, "system", "system", "Job finished")
        return output
    except Exception as exc:
        # Full traceback goes into the transcript (useful in the UI); stdout gets
        # a single line so expected failures don't look like crashes in the logs.
        detail = traceback.format_exc()
        print("[job %s] failed: %s: %s" % (job_id, type(exc).__name__, exc))
        add_event(job_id, "system", "error", detail)
        _mark_finished(job_id, "failed", error=str(exc))
        return None


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
ENQUEUE_TIMEOUT = 10       # a wedged redis must not hang the job that is submitting


def _enqueue_redis(job_id):
    """``(queued, reason)``: hand a job to arq, and say why when it did not go.

    ``asyncio.run`` cannot be called from inside a running event loop, and that is
    exactly where a child job is submitted from: the Manager's ``start_job`` tool and an
    automation's agent node both run inside the worker's loop. There the enqueue gets its
    own short-lived thread (and loop), so the job that is submitting is not blocked on
    redis and does not fail with "queue unavailable" when the queue is fine.
    """
    if not REDIS_URL:
        return False, "REDIS_URL is not set"

    async def enqueue():
        from arq import create_pool
        from arq.connections import RedisSettings

        pool = await create_pool(RedisSettings.from_dsn(REDIS_URL))
        try:
            return await pool.enqueue_job("run_agent_job", job_id)
        finally:
            await pool.aclose()

    def attempt():
        try:
            return asyncio.run(enqueue()) is not None, ""
        except Exception as exc:
            print("[jobs] redis enqueue failed (%s: %s)" % (type(exc).__name__, exc))
            return False, "%s: %s" % (type(exc).__name__, str(exc)[:200])

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return attempt()          # an ordinary request thread: no loop to protect

    # Inside a job. A second loop in another thread is the only sync way to reach redis
    # without touching the loop this job is already running on.
    box = {}

    def run():
        box["result"] = attempt()

    thread = threading.Thread(target=run, name="job-enqueue", daemon=True)
    thread.start()
    thread.join(ENQUEUE_TIMEOUT)
    if thread.is_alive():
        print("[jobs] redis enqueue timed out after %ss" % ENQUEUE_TIMEOUT)
        return False, "enqueue timed out after %ss" % ENQUEUE_TIMEOUT
    return box.get("result", (False, "the enqueue thread produced no result"))


def submit(job_id):
    """Queue a job for execution. Returns the backend that accepted it."""
    if JOB_BACKEND == "redis":
        queued, reason = _enqueue_redis(job_id)
        if queued:
            return "redis"
        add_event(job_id, "system", "error",
                  "Redis queue unavailable (%s); running in-process instead." % reason)
    get_runner().submit(job_id)
    return "local"


def get_runner():
    """Process-wide singleton runner."""
    global _runner
    with _runner_lock:
        if _runner is None:
            _runner = JobRunner()
        return _runner


# ---------------------------------------------------------------------------
# Built-in tasks
# ---------------------------------------------------------------------------
@task("echo")
async def _echo_task(job, emit):
    """Smoke-test task: streams a few events and completes. No API keys needed."""
    emit("system", "system", "echo task for: %s" % (job.get("prompt") or "(no prompt)"))
    for i in range(1, 4):
        emit("echo", "message", "step %d/3" % i)
        await asyncio.sleep(0)
    return "echo complete"
