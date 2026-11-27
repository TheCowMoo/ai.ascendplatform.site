"""Read-only page checks for the Manager and the review team.

The QA job in the incident had no way to load a URL. ``fetch_page`` gives it (and the
Manager) a structured report: status, title/meta, headings, links with their HTTP status,
images with their status and alt text, viewport/media-query markup, and placeholder
markers. ``fetch_page_html`` returns the raw source when markup must be diffed. Neither
claims to have rendered a viewport, and both are restricted to the client's domains.
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

HTML_DESCRIPTION = (
    "Fetch one live page on this client's site and return its raw HTML, for inspecting "
    "or diffing markup such as a header/nav splice. It is read-only and restricted to "
    "this client's domains. The response is a JSON object with url, status, content_type, "
    "bytes, characters, returned_characters, truncated and html. Read the html field "
    "exactly as returned; do not reconstruct markup from memory. It does not render "
    "layout, so visual checks still need a browser. If truncated is true, the body was "
    "too large (or the page exceeded the source cap) and you must say which part was not "
    "read rather than claiming a complete diff."
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


def _build_fetch_page_html(emit, job_id=None):
    async def fetch_page_html(url: str, max_chars: int = 0) -> str:
        """Load one allowed live page and return its raw HTML as JSON."""
        import webcheck

        job = jobs.get_job(job_id) if job_id else None
        org_id = (job or {}).get("org_id")
        if org_id is None:
            return ("This run is not attached to a client, so there is no site to read. "
                    "Pick a client in the workspace switcher first.")
        try:
            report = await asyncio.to_thread(
                webcheck.fetch_html, url, org_id, False, max_chars or 0)
        except webcheck.WebCheckError as exc:
            return "Could not read %s: %s" % (url, exc)
        if emit is not None:
            emit("tool", "tool_result",
                 "fetch_page_html: %s -> HTTP %s (%s chars%s)"
                 % (report.get("url"), report.get("status"),
                    report.get("returned_characters"),
                    ", truncated" if report.get("truncated") else ""))
        return json.dumps(report, indent=1)

    return fetch_page_html


register("fetch_page", DESCRIPTION, _build_fetch_page)
register("fetch_page_html", HTML_DESCRIPTION, _build_fetch_page_html)
