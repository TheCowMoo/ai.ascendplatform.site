"""Turn agent output into a deliverable the client can open.

Before this existed, a generated email lived only in the job transcript: the team could
write it, but nothing put it in the Emails tab, and the Operator could push HTML at a
*site* but never save a draft. That is why "create me an email" produced words in chat
and nothing else.

Ownership comes from the job row, not from the model: ``org_id`` and ``requester_id``
are read from ``jobs``, so a tool call can never write into another client's Library.
"""

import deliverables as deliverables_service
from db import get_connection

from . import register

DESCRIPTION = (
    "Save a finished piece of content as a draft the user can open in the console. "
    "Call this whenever you have produced something the user asked for - an email, a "
    "blog post, a funnel step, a website page - instead of only pasting it into the "
    "chat and stopping there. "
    "kind is one of: blog, funnel, email, website, asset. "
    "title is the name it will be listed under. "
    "content is the complete document, not a description of it: for email and website "
    "that means self-contained HTML with inline CSS (email clients strip <style> and "
    "<head>), no markdown code fences and no commentary around it. "
    "summary is one sentence describing it. "
    "It is always saved as a draft - the user decides when to publish or send."
)


def _job_context(job_id):
    """(org_id, requester_id) for the job, so the draft belongs to the right client."""
    if not job_id:
        return None, None
    with get_connection() as conn:
        row = conn.execute(
            "SELECT org_id, requester_id FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
    if not row:
        return None, None
    return row["org_id"], row["requester_id"]


def _build_save_deliverable(emit, job_id=None):
    async def save_deliverable(kind: str, title: str, content: str, summary: str = "") -> str:
        """Save content you have written as a draft the user can open in the console."""
        org_id, requester = _job_context(job_id)
        try:
            item = deliverables_service.create_deliverable(
                {
                    "kind": (kind or "").strip().lower(),
                    "title": title,
                    "content": content,
                    "summary": summary,
                    "status": "draft",
                    "job_id": job_id,
                },
                org_id=org_id,
                created_by=requester,
            )
        except deliverables_service.DeliverableError as exc:
            # A refused save must be visible to the model as a failure it can fix
            # (wrong kind, missing title) rather than a silent success.
            message = "Could not save: %s" % exc
            if emit is not None:
                emit("tool", "tool_error", "save_deliverable: " + message)
            return message

        tab = deliverables_service.TABS.get(item["kind"], "Library")
        message = (
            "Saved %s '%s' as draft #%s (%d characters). It is in the %s tab."
            % (item["kind"], item["title"], item["id"], len(item["content"] or ""), tab)
        )
        if emit is not None:
            emit("tool", "tool_result", "save_deliverable: " + message)
        return message

    return save_deliverable


register("save_deliverable", DESCRIPTION, _build_save_deliverable)
