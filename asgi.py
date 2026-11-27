"""
ASGI entry point for AscendPlatform (stage S4).

FastAPI now owns the routes that benefit most from true async handling — the SSE
job stream (which no longer holds a synchronous worker thread) and the provider
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


def docs_enabled():
    """Whether the FastAPI schema and Swagger UI are published.

    They describe every route and parameter, so they are an operator/development
    surface, not something an anonymous caller should enumerate by default.
    """
    raw = (os.environ.get("API_DOCS") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


DOCS_ENABLED = docs_enabled()

app = FastAPI(
    title="AscendPlatform",
    version="0.4.0",
    docs_url="/api/docs" if DOCS_ENABLED else None,
    openapi_url="/api/openapi.json" if DOCS_ENABLED else None,
    redoc_url=None,
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
    """Dependency: 401/403 unless the signed cookie still belongs to a live user.

    The cookie is only a hint about *who* signed in. Every authenticated request
    reloads the row, exactly like Flask's ``_session_account``: otherwise a
    suspended or deleted account kept streaming job transcripts until the cookie
    expired, and a role change was not seen at all. The tenant comes from that row,
    never from the cookie's stale copy.
    """
    data = flask_session(request)
    if not data.get("user_id"):
        raise HTTPException(status_code=401, detail="Unauthorized")
    user = flask_module.user_service.session_user(data["user_id"])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if user.get("status") != "active":
        raise HTTPException(
            status_code=403,
            detail="This client account is paused. Contact your agency to restore access.")
    if not flask_module.account_is_active(user.get("role"), user.get("org_id")):
        raise HTTPException(
            status_code=403,
            detail="This client account is paused. Contact your agency to restore access.")
    return user


def require_owner(request: Request):
    """Dependency: 403 unless the caller is the platform operator.

    ``/api/providers`` reports which providers *the platform's* keys configure. The
    agents are site-wide and run on the operator's keys, so that is operator
    information - and it used to answer with no session at all. The legacy ``admin``
    spelling is still an owner (``is_owner_role``), not a client role.
    """
    data = require_session(request)
    if not flask_module.user_service.is_owner_role(data.get("role")):
        raise HTTPException(status_code=403, detail="Forbidden")
    return data


# ---------------------------------------------------------------------------
# Migrated routes
# ---------------------------------------------------------------------------
@app.get("/api/providers", tags=["platform"])
def providers(session=Depends(require_owner)):
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
    # Scope the transcript to the caller's client. The dependency gives us the
    # freshly reloaded user row, not the cookie's stale role. Answer 404 rather than
    # 403 so a client cannot probe which job ids exist. A user with no org is confined
    # to nothing: comparing None != None would otherwise admit the NULL-org rows that
    # predate tenancy.
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Not found")
    owner = flask_module.user_service.is_owner_role(session.get("role"))
    if not owner:
        org = session.get("org_id")
        if org is None or job.get("org_id") != org:
            raise HTTPException(status_code=404, detail="Not found")

    max_seconds = int(os.environ.get("SSE_MAX_SECONDS", "600"))

    async def event_source():
        last_seq = after
        deadline = time.time() + max_seconds
        yield ": connected\n\n"
        while True:
            events = await asyncio.to_thread(jobs.get_events, job_id, last_seq)
            # The same white-label filter as /api/jobs/<id>/events. A client may keep
            # the stream open, so provider names must not slip through here.
            events = flask_module._client_events(events, owner)
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
