"""
arq worker for CMBGrowthAI agent jobs.

Run alongside the app so agent runs survive restarts and scale independently:

    JOB_BACKEND=redis REDIS_URL=redis://localhost:6379/0 python -m worker

In docker compose this is the ``worker`` service. With ``JOB_BACKEND=local``
(the default) the app runs jobs in-process and this module is unused.
"""

import os

# Load .env before importing anything that reads configuration at import time
# (agents -> tools). Without this a locally-run worker sees none of the keys,
# while docker compose works because it injects env_file: .env directly.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from arq.connections import RedisSettings

from db import init_db
import jobs  # noqa: F401  (registers the job tasks)
import agents  # noqa: F401  (registers agent_team, site_provision)
import manager  # noqa: F401  (registers chat)
import automations  # noqa: F401  (registers automation)
import review  # noqa: F401  (registers site_review)

REDIS_URL = (os.environ.get("REDIS_URL") or "redis://localhost:6379/0").strip()
JOB_TIMEOUT = int(os.environ.get("JOB_TIMEOUT", "900"))


async def run_agent_job(ctx, job_id):
    """arq entry point: execute one job by id."""
    await jobs.run_job(int(job_id))
    return {"job_id": job_id}


async def startup(ctx):
    # Make sure the schema exists even if the worker boots first.
    init_db()


async def shutdown(ctx):
    return None


class WorkerSettings:
    functions = [run_agent_job]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(REDIS_URL)
    max_jobs = int(os.environ.get("WORKER_MAX_JOBS", "4"))
    job_timeout = JOB_TIMEOUT
    keep_result = 300
    max_tries = 1


if __name__ == "__main__":
    # `python -m worker` (equivalent to `arq worker.WorkerSettings`).
    from arq import run_worker

    run_worker(WorkerSettings)
