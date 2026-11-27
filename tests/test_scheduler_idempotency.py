"""The coordinator must do each piece of work once, even with two app workers.

Run: .\\venv\\Scripts\\python.exe tests\\test_scheduler_idempotency.py

Two uvicorn workers each start the scheduler. The advisory lock makes one of them
skip a tick; the monthly grant is additionally serialised per client, and the
automation sweeps claim a row before they start it. These tests pin those guards.
"""

import asyncio
import json
import os
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="scheduler-once-"), "test.db").replace("\\", "/")
os.environ.pop("JOB_BACKEND", None)
os.environ.pop("REDIS_URL", None)
os.environ.pop("ALLOW_LIVE_ACTIONS", None)
os.environ["SCHEDULER_ENABLED"] = "0"

import automations  # noqa: E402
import db  # noqa: E402
import jobs  # noqa: E402
import orgs as orgs_service  # noqa: E402
import rates  # noqa: E402
import scheduler  # noqa: E402
import usage  # noqa: E402

db.init_db()

FAILED = []


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


def _org(name="Scheduler Client"):
    return orgs_service.create_org({"name": name})["id"]


def _automation(org, trigger_type="manual", trigger_config="{}", next_run_at=None):
    with db.get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO automations (org_id, name, enabled, trigger_type, "
            "trigger_config, steps, next_run_at) VALUES (?, ?, 1, ?, ?, ?, ?)",
            (org, "Test automation", trigger_type, trigger_config,
             json.dumps([{"id": "s1", "type": "stop", "title": "Done", "next": []}]),
             next_run_at))
        return cur.lastrowid


def test_the_coordinator_lock_is_exclusive():
    key = 778899
    held = threading.Event()
    release = threading.Event()

    def hold():
        with db.advisory_lock(key) as owned:
            held.set()
            release.wait(5)

    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    assert held.wait(5), "the holder never acquired the lock"
    with db.advisory_lock(key) as owned:
        check("a second worker skips the tick", owned, False)
    release.set()
    thread.join(5)
    with db.advisory_lock(key) as owned:
        check("the lock is released when the holder finishes", owned, True)
    print("the coordinator lock admits one worker at a time")


def test_the_monthly_grant_is_granted_once():
    org = _org("Monthly Grant Client")
    original = usage.plan_allowance
    usage.plan_allowance = lambda plan: 25.0
    try:
        first = usage.grant_monthly(period="2099-01")
        second = usage.grant_monthly(period="2099-01")
        balance_after_one_month = usage.balance(org)
        next_month = usage.grant_monthly(period="2099-02")
    finally:
        usage.plan_allowance = original
    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT delta_milli FROM credit_ledger WHERE org_id = ? AND reason = 'grant' "
            "AND period = '2099-01'", (org,)).fetchall()
    check("one grant row for the month", len(rows), 1)
    check("the second sweep adds nothing", second, 0)
    check("the allowance is in the balance", balance_after_one_month,
          25 * rates.MILLI_PER_CREDIT)
    check("a new month grants once", next_month, 1)
    print("a repetitive scheduler grants the monthly allowance exactly once")


def test_a_waiting_run_is_woken_once():
    org = _org("Wake Once Client")
    automation_id = _automation(org)
    with db.get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO automation_runs (automation_id, status, resume_at, wake_at, "
            "context) VALUES (?, 'waiting', 's1', '2000-01-01 00:00:00', '{}')",
            (automation_id,))
        run_id = cur.lastrowid

    submitted = []
    original_create, original_submit = jobs.create_job, jobs.submit
    jobs.create_job = lambda *a, **k: 9001
    jobs.submit = lambda job_id: submitted.append(job_id)
    try:
        first = automations.wake_due()
        second = automations.wake_due()
    finally:
        jobs.create_job, jobs.submit = original_create, original_submit

    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT status, wake_at FROM automation_runs WHERE id = ?", (run_id,)
        ).fetchone()
    check("the due run is woken once", first, 1)
    check("the overlapping sweep claims nothing", second, 0)
    check("one job is submitted", submitted, [9001])
    check("the run is queued with no wake time", (row["status"], row["wake_at"]),
          ("queued", None))
    print("a waiting run is claimed before its job is created")


def test_a_due_schedule_is_started_once():
    org = _org("Due Once Client")
    automation_id = _automation(org, trigger_type="schedule",
                                trigger_config=json.dumps({"every_minutes": 60}),
                                next_run_at="2000-01-01 00:00:00")
    calls = []
    original = automations.start
    automations.start = lambda automation_id, org_id, requester_id=None, context=None: (
        calls.append((automation_id, org_id)) or {"run_id": 1, "job_id": 2})
    try:
        first = automations.run_due()
        second = automations.run_due()
    finally:
        automations.start = original

    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT next_run_at FROM automations WHERE id = ?", (automation_id,)
        ).fetchone()
    check("the due turn starts once", first, 1)
    check("the next sweep is not due", second, 0)
    check("with the right automation", calls[0][0], automation_id)
    check("and the next turn is scheduled", row["next_run_at"] > "2000-01-01 00:00:00", True)
    print("a due schedule is claimed before the run starts")


def test_the_coordinator_runs_reviews_without_the_log_sync():
    calls = []
    originals = []

    def fake(name):
        def add(*args, **kwargs):
            calls.append(name)
            return 0

        return add

    targets = [
        (scheduler, "verify_pending"),
        (scheduler, "queue_alerts"),
        (scheduler, "queue_reviews"),
        (scheduler, "housekeeping"),
        (automations, "run_due"),
        (automations, "wake_due"),
        (usage, "sweep_voice_sessions"),
        (usage, "grant_monthly"),
    ]
    for module, name in targets:
        originals.append((module, name, getattr(module, name)))
        setattr(module, name, fake(name))
    try:
        scheduler._run_tick_tasks(verify=False)
    finally:
        for module, name, original in originals:
            setattr(module, name, original)

    check("weekly reviews are part of the coordinator", "queue_reviews" in calls, True)
    check("DNS checks can still be turned off", "verify_pending" in calls, False)
    print("reviews run even when the log sync is off")


def test_a_legacy_ungated_graph_is_refused_at_run_time():
    org = _org("Legacy Bypass Client")
    steps = [
        {"id": "start", "type": "condition", "title": "Branch",
         "config": {"fact": "drafts", "op": ">", "value": 1},
         "next": ["gate", "send"]},
        {"id": "gate", "type": "gate", "title": "Approve", "next": ["send"]},
        {"id": "send", "type": "tool", "title": "Send",
         "config": {"tool": "send_email"}, "next": []},
    ]
    with db.get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO automations (org_id, name, enabled, trigger_type, "
            "trigger_config, steps) VALUES (?, ?, 1, 'manual', '{}', ?)",
            (org, "Legacy bypass", json.dumps(steps)))
        automation_id = cur.lastrowid
    job_id = jobs.create_job(
        "Legacy automation", json.dumps({"automation_id": automation_id}),
        None, "selector", "automation", org_id=org)
    job = jobs.get_job(job_id)
    try:
        asyncio.run(automations.run_task(job, lambda *a, **k: None))
        check("a legacy bypass is refused", False, True)
    except RuntimeError as exc:
        check("a legacy bypass is refused", "approval gate" in str(exc), True)
    print("a pre-fix graph cannot run its ungated branch")


def test_scheduler_can_be_disabled():
    original = os.environ.get("SCHEDULER_ENABLED")
    try:
        os.environ["SCHEDULER_ENABLED"] = "0"
        check("zero turns the scheduler off", scheduler.scheduler_enabled(), False)
        os.environ["SCHEDULER_ENABLED"] = "1"
        check("one turns it on", scheduler.scheduler_enabled(), True)
    finally:
        if original is None:
            os.environ.pop("SCHEDULER_ENABLED", None)
        else:
            os.environ["SCHEDULER_ENABLED"] = original
    print("an operator can turn background work off in one setting")


if __name__ == "__main__":
    test_the_coordinator_lock_is_exclusive()
    test_the_monthly_grant_is_granted_once()
    test_a_waiting_run_is_woken_once()
    test_a_due_schedule_is_started_once()
    test_the_coordinator_runs_reviews_without_the_log_sync()
    test_a_legacy_ungated_graph_is_refused_at_run_time()
    test_scheduler_can_be_disabled()
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nSCHEDULER IDEMPOTENCY OK - one worker, one grant, one run")
