"""
ASGI entry point for CMBGrowthAI (stage S4).

FastAPI now owns the routes that benefit most from true async handling — the SSE
job stream (which no longer holds a gunicorn worker thread) and the provider
catalogue — while every other route is still served by the existing Flask app,
mounted at "/" via a2wsgi.

Starlette matches routes in order, so FastAPI routes take precedence and the Flask
mount catches everything else. That is what makes the migration incremental:
route groups move over one at a time, with no big-bang rewrite and no downtime
in behaviour.

Production:  uvicorn asgi:app --host 0.0.0.0 --port 8000
Dev:         uvicorn asgi:app --reload
"""

import asyncio
import json
import os
import time

from a2wsgi import WSGIMiddleware
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from flask.sessions import SecureCookieSessionInterface

import app as flask_module
import jobs
import llm

flask_app = flask_module.app

app = FastAPI(
    title="AscendPlatform",
    version="0.4.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

# ---------------------------------------------------------------------------
# Session bridge: FastAPI reads the same signed session cookie Flask writes, so
# both halves of the app share one login. No new auth scheme, no duplicate users.
# ---------------------------------------------------------------------------
_session_serializer = SecureCookieSessionInterface().get_signing_serializer(flask_app)
SESSION_COOKIE = flask_app.config.get("SESSION_COOKIE_NAME", "session")


def flask_session(request: Request):
    """Decode Flask's signed session cookie ({} when absent or tampered with)."""
    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        return {}
    try:
        return _session_serializer.loads(cookie) or {}
    except Exception:
        return {}


def require_session(request: Request):
    """Dependency: 401 unless a valid logged-in session cookie is present."""
    data = flask_session(request)
    if not data.get("user_id"):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return data


# ---------------------------------------------------------------------------
# Migrated routes
# ---------------------------------------------------------------------------
@app.get("/api/providers", tags=["platform"])
def providers():
    """Provider status; never exposes secrets."""
    return {
        "providers": llm.list_providers(),
        "configured": llm.configured_providers(),
        "default": llm.DEFAULT_PROVIDER,
        "gateway": llm.gateway_info(),
    }


@app.get("/api/jobs/{job_id}/stream", tags=["jobs"])
async def job_stream(job_id: int, after: int = 0, session=Depends(require_session)):
    """Server-Sent Events stream of a job's transcript.

    Fully async: database reads run in a thread via ``asyncio.to_thread`` so the
    event loop stays free and the connection does not pin a worker process.
    """
    if not jobs.get_job(job_id):
        raise HTTPException(status_code=404, detail="Not found")

    max_seconds = int(os.environ.get("SSE_MAX_SECONDS", "600"))

    async def event_source():
        last_seq = after
        deadline = time.time() + max_seconds
        yield ": connected\n\n"
        while True:
            events = await asyncio.to_thread(jobs.get_events, job_id, last_seq)
            for event in events:
                last_seq = event["seq"]
                yield "id: %d\nevent: %s\ndata: %s\n\n" % (
                    event["seq"],
                    event["type"],
                    json.dumps(event, default=str),
                )
            job = await asyncio.to_thread(jobs.get_job, job_id)
            status = job["status"] if job else "missing"
            if status == "missing" or status in jobs.TERMINAL_STATUSES:
                yield "event: end\ndata: %s\n\n" % json.dumps({"status": status})
                return
            if time.time() > deadline:
                yield "event: end\ndata: %s\n\n" % json.dumps(
                    {"status": status, "reason": "timeout"}
                )
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Everything else is still Flask, mounted at "/" (matched last).
# ---------------------------------------------------------------------------
app.mount("/", WSGIMiddleware(flask_app))
