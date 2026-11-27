"""Job event appends and cancel calls are safe under the scheduler.

Run: .\\venv\\Scripts\\python.exe tests\\test_jobs_safety.py

``add_event`` serialises MAX(seq)+1 per job on PostgreSQL, and ``cancel_job`` now
says whether it changed anything instead of reporting success for a finished job.
"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="jobs-safety-"), "test.db").replace("\\", "/")
os.environ["SCHEDULER_ENABLED"] = "0"

import db  # noqa: E402
import jobs  # noqa: E402

db.init_db()

FAILED = []


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


def test_appends_take_a_per_job_lock():
    job_id = jobs.create_job("Events", "x", None, "selector", "echo", org_id=None)
    calls = []
    original = jobs.advisory_xact_lock
    jobs.advisory_xact_lock = lambda conn, key: calls.append(key)
    try:
        jobs.add_event(job_id, "system", "message", "first")
    finally:
        jobs.advisory_xact_lock = original
    check("the per-job lock is taken",
          calls, [jobs.JOB_EVENT_LOCK_BASE + job_id])
    events = jobs.get_events(job_id)
    check("the event was appended", (events[0]["seq"], events[0]["content"]),
          (1, "first"))


def test_cancel_only_changes_a_live_job():
    job_id = jobs.create_job("Cancel me", "x", None, "selector", "echo", org_id=None)
    check("a queued job is cancelled", jobs.cancel_job(job_id), True)
    check("cancelling it again does nothing", jobs.cancel_job(job_id), False)

    finished = jobs.create_job("Finished", "x", None, "selector", "echo", org_id=None)
    with db.get_connection() as conn:
        conn.execute("UPDATE jobs SET status = 'done' WHERE id = ?", (finished,))
    check("a finished job is not cancelled", jobs.cancel_job(finished), False)
    check("a missing job says so", jobs.cancel_job(999999), False)


if __name__ == "__main__":
    test_appends_take_a_per_job_lock()
    test_cancel_only_changes_a_live_job()
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nJOBS SAFETY OK - one seq writer per job, honest cancel")
