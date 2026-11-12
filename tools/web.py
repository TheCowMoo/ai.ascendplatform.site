"""Read-only page checks for the Manager and the review team.

The QA job in the incident had no way to load a URL. This tool gives it (and the Manager)
the actual page: status, title/meta, headings, links with their HTTP status, images with
their status and alt text, viewport/media-query markup, and placeholder markers. It never
claims to have rendered a viewport.
"""

import asyncio
import json

import jobs
from tools import register

DESCRIPTION = (
    "Fetch a live page on this client's site and report what can be checked without a "
    "browser: HTTP status, title, meta description, H1/H2, links and images with their "
    "HTTP status, viewport/media-query markup, placeholder text, and whether the page is "
    "the starter placeholder. It is read-only. It cannot verify visual layout at a "
    "desktop or mobile width - say so when reporting."
)


def _build_fetch_page(emit, job_id=None):
    async def fetch_page(url: str) -> str:
        """Load one live page on this client's site and return a JSON check report."""
        import webcheck

        job = jobs.get_job(job_id) if job_id else None
        org_id = (job or {}).get("org_id")
        if org_id is None:
            return ("This run is not attached to a client, so there is no site to check. "
                    "Pick a client in the workspace switcher first.")
        try:
            report = await asyncio.to_thread(webcheck.inspect, url, org_id)
        except webcheck.WebCheckError as exc:
            return "Could not check %s: %s" % (url, exc)
        if emit is not None:
            emit("tool", "tool_result",
                 "fetch_page: %s -> HTTP %s" % (report.get("url"), report.get("status")))
        return json.dumps(report, indent=1)

    return fetch_page


register("fetch_page", DESCRIPTION, _build_fetch_page)
