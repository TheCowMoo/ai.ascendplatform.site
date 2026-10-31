"""The Studio: one artifact on the canvas, the conversation beside it.

A Studio message is different from an ordinary Assistant turn because it names the
thing on screen. The browser sends only ``{"kind": "deliverable", "id": 42}`` - the
kind and the id, never the title or the content. This module turns that into the
block the Manager reads; the route in ``app.py`` is what decides whether the caller
may touch that row at all.

Why the block is built here rather than in the route: it is the contract between the
canvas and the model. Keeping it a pure function means the wording, the id and the
content cap can be tested without a request, a session or a database.

The block deliberately opens with the id and the instruction, and only then includes
the current document. ``/api/voice/tool`` copies the previous prompt with a hard
4000-character cap when the team asks a question, so anything after that point is
lost on the follow-up - the id must never be the part that is lost.
"""

MAX_CONTENT = 12000

# Site records (write_site_content) join this tuple when the canvas learns to show
# them; until then a Studio message names a generated draft and nothing else.
KINDS = ("deliverable",)


class StudioError(Exception):
    """Invalid artifact input. The message is safe to show the user."""


def normalise(raw):
    """``(kind, id)`` for the artifact a Studio request names, or None.

    Anything the browser sends beyond the kind and the id is discarded: a request can
    point at an artifact, never describe one.
    """
    if raw in (None, ""):
        return None
    if not isinstance(raw, dict):
        raise StudioError(
            'artifact must look like {"kind": "deliverable", "id": 42}.')
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in KINDS:
        raise StudioError(
            "The Studio can edit a saved draft; artifact kind must be one of: %s."
            % ", ".join(KINDS))
    try:
        artifact_id = int(raw.get("id"))
    except (TypeError, ValueError):
        raise StudioError("artifact id must be a number.")
    if artifact_id <= 0:
        raise StudioError("artifact id must be a number.")
    return kind, artifact_id


def deliverable_block(item, content_limit=MAX_CONTENT):
    """The prompt block that binds a turn to one existing draft.

    ``content_limit`` exists for the voice path: its follow-up truncates the previous
    prompt at 4000 characters, so a smaller block is the one that survives a spoken
    question intact.
    """
    item = item or {}
    deliverable_id = int(item.get("id") or 0)
    kind = item.get("kind") or "draft"
    title = item.get("title") or "(untitled)"
    status = item.get("status") or "draft"
    content = (item.get("content") or "").strip()
    if content:
        limit = max(0, int(content_limit))
        if len(content) > limit:
            content = content[:limit] + "\n…(truncated - the document continues)"
    else:
        content = "(this draft is empty)"

    return (
        "[Studio context - the user is looking at deliverable #%d \"%s\" "
        "(kind: %s, status: %s). Change that draft: call save_deliverable with "
        "deliverable_id=%d, kind=%r, title=%r, and content = the complete updated "
        "document. Do not create a new draft, and do not reply with a diff. Only make a "
        "new draft if the user explicitly asks for a different page.\n\n"
        "Current content of #%d:\n%s]"
        % (deliverable_id, title, kind, status, deliverable_id, kind, title,
           deliverable_id, content)
    )
