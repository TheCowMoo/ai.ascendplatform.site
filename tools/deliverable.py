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


def _artifact_images(job_id):
    """The images this job saved, as URLs the browser can actually load."""
    import os

    root = (os.environ.get("ARTIFACTS_DIR") or "artifacts").rstrip("/\\")
    folder = os.path.join(root, "images", "job-%s" % (job_id or "manual"))
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    return [
        "/artifacts/images/job-%s/%s" % (job_id or "manual", name)
        for name in names
        if name.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
    ]


def _fill_image_placeholders(content, job_id):
    """Substitute real URLs for [INSERT IMAGE URL] markers, in order.

    Instructions alone do not stop a model leaving fill-in markers, and a draft full of
    them looks finished while shipping broken. The job has already downloaded the
    photos, so the server can finish the document itself.
    """
    import re

    images = _artifact_images(job_id)
    if not images:
        return content
    marker = re.compile(r"\[(?:INSERT|Insert|insert)[^\]]{0,60}(?:URL|Url|url)[^\]]{0,20}\]")
    counter = iter(images + images * 5)  # reuse the pool if there are more markers
    return marker.sub(lambda _match: next(counter, ""), content or "")


def _placeholders(content):
    """Image URL markers still left, i.e. the document is not publishable."""
    import re

    found = re.findall(r"\[(?:INSERT|Insert|insert)[^\]]{0,60}(?:URL|Url|url)[^\]]{0,20}\]",
                       content or "")
    return list(dict.fromkeys(found))


def _draft_from_this_job(job_id, org_id, kind, title):
    """This job's earlier draft with the same kind and title, if there is one."""
    if not job_id:
        return None
    wanted = (title or "").strip().lower()
    for item in deliverables_service.list_deliverables(kind, org_id):
        if item.get("job_id") == job_id and (item["title"] or "").strip().lower() == wanted:
            return item
    return None


def _build_save_deliverable(emit, job_id=None):
    async def save_deliverable(kind: str, title: str, content: str = "", summary: str = "") -> str:
        """Save content you have written as a draft the user can open in the console."""
        if not (content or "").strip():
            # A missing content argument used to surface as a pydantic validation error,
            # which tells the model nothing it can act on. Say it plainly instead.
            return (
                "Nothing was saved: content is required. Pass the complete document as "
                "content - self-contained HTML for an email or a website page."
            )
        content = _fill_image_placeholders(content, job_id)
        placeholders = _placeholders(content)
        if placeholders:
            # Only reachable when the job has no downloaded images to substitute: a
            # draft full of markers looks finished and ships broken.
            return (
                "Nothing was saved: content still contains %s and this job has no "
                "downloaded images. Call search_photos, then fetch_photo with a "
                "download_url, or remove that <img> entirely, then save again."
                % ", ".join(placeholders[:4])
            )
        org_id, requester = _job_context(job_id)
        # The team often saves before the images are chosen, then again once they are.
        # Updating this job's own draft keeps one row instead of a duplicate per pass.
        existing = _draft_from_this_job(job_id, org_id, kind, title)
        try:
            if existing:
                item = deliverables_service.update_deliverable(
                    existing["id"],
                    {"content": content, "summary": summary or existing.get("summary") or ""},
                )
                verb = "Updated"
            else:
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
                verb = "Saved"
        except deliverables_service.DeliverableError as exc:
            # A refused save must be visible to the model as a failure it can fix
            # (wrong kind, missing title) rather than a silent success.
            message = "Could not save: %s" % exc
            if emit is not None:
                emit("tool", "tool_error", "save_deliverable: " + message)
            return message

        tab = deliverables_service.TABS.get(item["kind"], "Library")
        message = (
            "%s %s '%s' as draft #%s (%d characters). It is in the %s tab."
            % (verb, item["kind"], item["title"], item["id"], len(item["content"] or ""), tab)
        )
        if emit is not None:
            emit("tool", "tool_result", "save_deliverable: " + message)
        return message

    return save_deliverable


register("save_deliverable", DESCRIPTION, _build_save_deliverable)
