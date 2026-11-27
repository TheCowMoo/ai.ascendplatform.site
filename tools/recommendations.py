"""Read-only recommendation tool for the Manager.

The dashboard and Studio read the same cached batch through the API. This tool gives
the chat a way to answer "what should I do next?" without inventing a second set of
ideas or spending a model call on every turn.
"""

import json

import recommendations as recommendations_service
from tools import access, register


def _build_recommend_next_steps(emit, job_id=None):
    async def recommend_next_steps() -> str:
        """Return this client's current evidence-backed next steps."""
        ctx = access.context(job_id)
        if ctx["org_id"] is None:
            return "Pick a client first - recommendations belong to a client."
        data = recommendations_service.get(
            ctx["org_id"],
            include_admin=access.is_manager(ctx["role"]),
            requester_id=ctx["requester_id"],
            queue_ai=False,
        )
        rows = data.get("recommendations") or []
        if not rows:
            return ("No specific recommendations right now. Ask the client what they "
                    "want to improve, or start from the Business details.")
        return json.dumps([{
            "key": item.get("key"),
            "title": item.get("title"),
            "why": item.get("why"),
            "action": item.get("action_text"),
            "evidence": item.get("evidence") or [],
            "open": item.get("href") or "",
        } for item in rows], indent=1)

    return recommend_next_steps


register("recommend_next_steps",
         "Return this client's current evidence-backed next steps. Read-only: use it "
         "when the user asks what to do next, then explain or act only after they choose.",
         _build_recommend_next_steps)
