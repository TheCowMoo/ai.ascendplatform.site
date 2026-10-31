"""The queue hand-off: it must work from a request thread *and* from inside a job.

Run: .\\venv\\Scripts\\python.exe tests\\test_job_enqueue.py

``asyncio.run`` cannot be called from a running event loop, and a child job is submitted
from exactly that place - the Manager's ``start_job`` tool and an automation's agent node
both execute inside the worker's loop. The old code raised, caught its own RuntimeError
and wrote "Redis queue unavailable" into the transcript while redis was perfectly fine;
the child still ran, but in a thread inside the worker, outside arq. These checks pin the
second-loop path and the reason a genuine outage now reports.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.pop("ALLOW_LIVE_ACTIONS", None)

import arq  # noqa: E402

import jobs  # noqa: E402

FAILED = []


def check(label, got, want):
    if got == want:
        print("  ok - %s" % label)
        return
    FAILED.append(label)
    print("  FAILED - %s: %r != %r" % (label, got, want))


class FakePool:
    """Enough of an arq pool for one enqueue; no redis, no network."""

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []
        self.closed = False

    async def enqueue_job(self, name, job_id):
        if self.fail:
            raise ConnectionError("redis is down")
        self.calls.append((name, int(job_id)))
        return object()

    async def aclose(self):
        self.closed = True


class FakePoolFactory:
    def __init__(self, pool):
        self.pool = pool

    async def __call__(self, settings):
        return self.pool


def with_queue(pool):
    """Install a fake ``arq.create_pool`` and return the original for the finally."""
    original = arq.create_pool
    arq.create_pool = FakePoolFactory(pool)
    return original


def test_enqueue_works_from_a_request_thread_and_inside_a_job():
    original_url = jobs.REDIS_URL
    jobs.REDIS_URL = "redis://queue.test:6379/0"
    pool = FakePool()
    original_create = with_queue(pool)
    try:
        queued, reason = jobs._enqueue_redis(1)
        check("a request thread enqueues", (queued, reason), (True, ""))

        async def inside_a_job():
            # The worker's loop is running here, exactly as it is when start_job or an
            # automation's agent node submits a child.
            return jobs._enqueue_redis(2)

        queued, reason = asyncio.run(inside_a_job())
        check("a job can enqueue its own child", (queued, reason), (True, ""))
        check("both reached the queue", pool.calls,
              [("run_agent_job", 1), ("run_agent_job", 2)])
        check("the pool is closed every time", pool.closed, True)
    finally:
        arq.create_pool = original_create
        jobs.REDIS_URL = original_url


def test_a_real_outage_reports_its_reason():
    original_url = jobs.REDIS_URL
    jobs.REDIS_URL = "redis://queue.test:6379/0"
    original_create = with_queue(FakePool(fail=True))
    try:
        async def inside_a_job():
            return jobs._enqueue_redis(3)

        queued, reason = asyncio.run(inside_a_job())
    finally:
        arq.create_pool = original_create
        jobs.REDIS_URL = original_url
    check("a failure is not enqueued", queued, False)
    check("and says what went wrong",
          "ConnectionError" in reason and "redis is down" in reason, True)


def test_submit_reports_the_reason_in_the_transcript():
    original = (jobs.REDIS_URL, jobs.JOB_BACKEND, jobs.add_event, jobs.get_runner)
    events = []
    ran = []

    class Runner:
        def submit(self, job_id):
            ran.append(job_id)

    jobs.REDIS_URL = ""                 # the configuration mistake, not an outage
    jobs.JOB_BACKEND = "redis"
    jobs.add_event = lambda job_id, source, kind, content: events.append((kind, content))
    jobs.get_runner = lambda: Runner()
    try:
        backend = jobs.submit(9)
    finally:
        jobs.REDIS_URL, jobs.JOB_BACKEND, jobs.add_event, jobs.get_runner = original
    check("it falls back to the local runner", (backend, ran), ("local", [9]))
    check("and the transcript names the reason",
          any("REDIS_URL is not set" in content for kind, content in events), True)
    check("as an error, because the job did not go where it was meant to", events[0][0], "error")


def main():
    test_enqueue_works_from_a_request_thread_and_inside_a_job()
    test_a_real_outage_reports_its_reason()
    test_submit_reports_the_reason_in_the_transcript()
    if FAILED:
        print("\n%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
        raise SystemExit(1)
    print("\nJOB ENQUEUE OK - the queue works from inside a job, and a failure says why")


if __name__ == "__main__":
    main()
