"""
Tool registry for agent runs.

Agents declare which tools they may call in the roster (``agents.tools`` JSON).
This module maps those names to AutoGen ``FunctionTool`` objects.

Stage S3 ships only safe, side-effect-free built-ins. The side-effecting tools
(Sites provisioning, SSH, email, publishing) arrive in S5/S6 and must plug in
through the same ``register``/``resolve`` interface, so the command policy and
approval gates can be applied uniformly.
"""

from autogen_core.tools import FunctionTool

import os

_REGISTRY = {}


def safe_filename(name, default="file", suffixes=()):
    """Sanitise a model-supplied filename for use inside the artifacts directory.

    Strips any directory component and parent traversal, and guarantees one of
    the allowed suffixes. Shared by the media and Pexels tools.
    """
    base = os.path.basename(name or default).replace("..", "_").strip() or default
    if suffixes and not base.lower().endswith(tuple(suffixes)):
        base += suffixes[0]
    return base


def register(name, description, builder):
    """Register a tool. ``builder(emit, job_id=None)`` returns the callable to expose."""
    _REGISTRY[name] = {"name": name, "description": description, "build": builder}


def available():
    """Sorted names of the registered tools."""
    return sorted(_REGISTRY)


def describe():
    """Metadata for the admin UI."""
    return [{"name": spec["name"], "description": spec["description"]} for spec in _REGISTRY.values()]


def resolve(names, emit=None, job_id=None):
    """Build ``FunctionTool`` objects for ``names`` (unknown names are skipped)."""
    resolved = []
    for name in names or []:
        spec = _REGISTRY.get(name)
        if spec is None:
            continue
        function = spec["build"](emit, job_id)
        resolved.append(FunctionTool(function, description=spec["description"], name=name))
    return resolved


def unknown(names):
    """Tool names that are not registered (surfaced as a warning in the UI)."""
    return [name for name in (names or []) if name not in _REGISTRY]


# ---------------------------------------------------------------------------
# Built-in tools
# ---------------------------------------------------------------------------
def _build_save_note(emit, job_id=None):
    async def save_note(text: str) -> str:
        """Save a short note to the job transcript (decisions, URLs, next steps)."""
        message = str(text)
        if emit is not None:
            emit("tool", "tool_result", "save_note: " + message)
        return "Saved note (%d characters)." % len(message)

    return save_note


def _build_current_time(emit, job_id=None):
    async def current_time() -> str:
        """Return the current UTC timestamp as 'YYYY-MM-DD HH:MM:SS'."""
        from db import now_str

        stamp = now_str()
        if emit is not None:
            emit("tool", "tool_result", "current_time: " + stamp)
        return stamp

    return current_time


register(
    "save_note",
    "Save a short note to the job transcript (decisions, URLs, next steps).",
    _build_save_note,
)
register(
    "current_time",
    "Return the current UTC timestamp as 'YYYY-MM-DD HH:MM:SS'.",
    _build_current_time,
)

# Side-effecting tools live in their own modules and self-register on import.
# They all route through tools.policy and actions.gate, so they are equally safe
# by default (dry-run) and equally auditable.
from . import site as _site_tools  # noqa: E402,F401
from . import media as _media_tools  # noqa: E402,F401
from . import pexels as _pexels_tools  # noqa: E402,F401
from . import mail as _mail_tools  # noqa: E402,F401
from . import deliverable as _deliverable_tools  # noqa: E402,F401
