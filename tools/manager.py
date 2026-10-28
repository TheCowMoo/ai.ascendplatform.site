"""Tools for the floating manager: read the platform, edit drafts, delegate work.

Two rules baked in. Ownership comes from the **job row**, never from the model's
wording, so it cannot be talked into another client's data. And nothing outward-facing
is granted here - sending and publishing stay behind the team's own gated tools, because
a chat turn that blocks for five minutes on an approval is a worse experience than a job
you can watch.
"""

import json

import deliverables as deliverables_service
import sites as sites_service
from db import get_connection

from . import register


def _context(job_id):
    """(org_id, requester_id) for the job the tool call belongs to."""
    if not job_id:
        return None, None
    with get_connection() as conn:
        row = conn.execute(
            "SELECT org_id, requester_id FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
    if not row:
        return None, None
    return row["org_id"], row["requester_id"]


def _build_list_sites(emit, job_id=None):
    async def list_sites() -> str:
        """List this client's websites with status and URL."""
        org_id, _ = _context(job_id)
        rows = sites_service.list_sites(org_id)
        if not rows:
            return "No sites yet for this client."
        return json.dumps(
            [{"domain": r["domain"], "status": r["status"], "url": r["url"]} for r in rows],
            indent=1,
        )

    return list_sites


def _build_list_deliverables(emit, job_id=None):
    async def list_deliverables_of_kind(kind: str = "") -> str:
        """List recent drafts, optionally filtered to one kind (email, blog, website...)."""
        org_id, _ = _context(job_id)
        rows = deliverables_service.list_deliverables(kind.strip().lower() or None, org_id)
        if not rows:
            return "No drafts yet."
        return json.dumps(
            [{"id": r["id"], "kind": r["kind"], "title": r["title"], "status": r["status"]}
             for r in rows[:15]],
            indent=1,
        )

    return list_deliverables_of_kind


def _build_get_metrics(emit, job_id=None):
    async def get_metrics(days: int = 7) -> str:
        """This client's traffic for the last N days, against the window before it."""
        import review

        org_id, _ = _context(job_id)
        if org_id is None:
            return "This chat is not attached to a client, so there are no metrics."
        brief = review.brief(org_id, days=max(1, min(90, int(days or 7))))
        return json.dumps(brief, indent=1)

    return get_metrics


def _build_start_job(emit, job_id=None):
    async def start_job(prompt: str, title: str = "") -> str:
        """Hand work to the agent team: writing, reviewing a site, building a page."""
        import jobs

        if not (prompt or "").strip():
            return "A prompt is required."
        org_id, requester = _context(job_id)
        if org_id is None:
            return (
                "I can't start work that belongs to no client - it would never show up "
                "in their Jobs or Library. Pick a client in the workspace switcher, then "
                "ask me again."
            )
        new_id = jobs.create_job(
            title.strip() or prompt.strip()[:60],
            prompt.strip(),
            requester_id=requester,
            team_mode="selector",
            task_name="agent_team",
            org_id=org_id,
        )
        jobs.submit(new_id)      # a created job that is never submitted queues forever
        return "Started job #%d. Open it in Jobs to follow along." % new_id

    return start_job


def _build_propose_automation(emit, job_id=None):
    async def propose_automation(name: str, steps: str, automation_id: int = 0) -> str:
        """Draft an automation graph for the client to review, then apply.

        ``steps`` is a JSON array of node objects, each with ``id``, ``type``, ``title``
        and ``next`` (plus ``config`` for an agent or condition node) - the tool schema
        needs one type per argument, so a JSON string is what the model sends.

        Nothing becomes live: the draft goes into the proposals queue and a human presses
        Apply. The validator's complaints come back verbatim - fix them and call again
        with the whole graph, because a half-corrected graph is a different graph.
        """
        import json

        import automations

        org_id, requester = _context(job_id)
        if org_id is None:
            return ("I can't draft an automation without a client. Pick one in the "
                    "workspace switcher, then ask again.")
        if isinstance(steps, str):
            try:
                steps = json.loads(steps)
            except ValueError as exc:
                return ("Those steps are not valid JSON (%s). Pass a list of node objects, "
                        "each with id, type, title and next." % exc)
        if not isinstance(steps, list) or not steps:
            return ("Pass steps as a list of node objects: id, type, title, next, and "
                    "config for an agent or condition node.")
        draft, problems = automations.propose(
            org_id, name, steps, automation_id=int(automation_id) or None,
            created_by=requester, job_id=job_id,
        )
        if problems:
            return ("Not saved. Fix these and call again with the complete graph: "
                    + " ".join(problems))
        return ("Drafted it as proposal #%d - %d steps. It is not live: open Automations, "
                "preview it, and press Apply to save it."
                % (draft["id"], len(draft["steps"])))

    return propose_automation


register("propose_automation",
         "Draft an automation graph for the client to preview and apply. Nothing goes live.",
         _build_propose_automation)


register("list_sites", "List this client's websites with status and URL.",         _build_list_sites)
register("list_deliverables",
         "List recent drafts, optionally filtered to one kind (email, blog, website).",
         _build_list_deliverables)
register("get_metrics",
         "This client's traffic for the last N days, compared with the window before it.",
         _build_get_metrics)
register("start_job",
         "Hand work to the agent team - writing, reviewing a site, building a page. "
         "Returns the new job id.",
         _build_start_job)
