"""
Designatable agent roster and the AutoGen team that runs it.

Each row of the ``agents`` table becomes one AutoGen ``AssistantAgent`` with its
own provider, model and tool set — so "Gemini = image creator" is a data change
made in the admin UI, not a code change. ``run_team`` is registered as the
``agent_team`` job task, so it executes on whichever backend the job engine uses
(in-process thread or the arq/Redis worker).
"""

import json
import os

from db import IntegrityError, get_connection, now_str, seed_lock
import jobs
import llm
import tools as tool_registry

TEXT_FIELDS = ("name", "role", "provider", "model", "system_prompt")

#: Seeded on first run. Providers are picked to match each role's strengths.
DEFAULT_ROSTER = [
    {
        "name": "Planner",
        "role": "Break the request into an ordered plan the team can execute.",
        "provider": "anthropic",
        "model": "",
        "system_prompt": (
            "You are the Planner. Read the task and produce a short, ordered plan "
            "(numbered steps) naming which teammate does what and which files or "
            "artefacts are expected. Be concrete and brief. Do not write the final "
            "deliverable yourself."
        ),
        "tools": [],
        "turn_order": 1,
    },
    {
        "name": "Researcher",
        "role": "Gather facts and structure the source material.",
        "provider": "gemini",
        "model": "",
        "system_prompt": (
            "You are the Researcher. Assemble the facts, structure and terminology "
            "needed for the task. Summarise findings as bullet points. Flag anything "
            "you are unsure about instead of guessing."
        ),
        "tools": ["current_time"],
        "turn_order": 2,
    },
    {
        "name": "Copywriter",
        "role": "Write the words: pages, emails, posts and calls to action.",
        "provider": "anthropic",
        "model": "",
        "system_prompt": (
            "You are the Copywriter. Produce the finished copy in a clear, warm, "
            "benefit-led voice. Use the Business Profile for brand voice and facts. "
            "Return ready-to-publish text, not descriptions of it."
        ),
        "tools": ["save_deliverable", "search_photos"],
        "turn_order": 3,
    },
    {
        "name": "Coder",
        "role": "Write the code and markup.",
        "provider": "deepseek",
        "model": "",
        "system_prompt": (
            "You are the Coder. Write complete, runnable code or markup that fulfils "
            "the plan. State the filename of each artefact and include the full "
            "contents. Do not leave placeholders."
        ),
        "tools": [],
        "turn_order": 4,
    },
    {
        "name": "Image Artist",
        "role": "Create the visual assets.",
        "provider": "gemini",
        "model": "",
        "system_prompt": (
            "You are the Image Artist. For real-world subjects - people, places, "
            "products, offices - use search_photos to find suitable stock photography "
            "and fetch_photo to save it, and always report the credit line it returns. "
            "For brand marks, illustrations or anything that does not exist yet, use "
            "generate_image instead. Name the file path for every asset. In HTML, "
            "reference the absolute https URL that search_photos returned - never a "
            "local disk path, which renders as a broken image."
        ),
        "tools": ["search_photos", "fetch_photo", "generate_image"],
        "turn_order": 5,
    },
    {
        "name": "Reviewer",
        "role": "Check the work and approve it.",
        "provider": "gemini",
        "model": "",
        "system_prompt": (
            "You are the Reviewer. Check the team's output against the task: "
            "correctness, completeness and clarity. List any problems and ask for "
            "fixes. When everything is satisfactory, reply with a short summary "
            "followed by the single word APPROVE on its own line."
        ),
        "tools": ["current_time"],
        "turn_order": 6,
    },
    {
        "name": "Operator",
        "role": "Carry out the approved actions and report results.",
        "provider": "anthropic",
        "model": "",
        "system_prompt": (
            "You are the Operator. Once the work is approved, carry out the actions "
            "the task requires using your tools, and report what you did. If an "
            "action is unavailable, say so plainly instead of pretending."
        ),
        "tools": ["provision_site", "check_site_dns", "write_site_content", "list_sites",
                  "search_photos", "fetch_photo", "generate_image", "send_email",
                  "save_deliverable", "save_note", "current_time"],
        "turn_order": 7,
    },
]

def _as_dict(row):
    return {k: row[k] for k in row.keys()}


def _agent_out(row):
    """Serialise an agent row, parsing the JSON tools column."""
    out = _as_dict(row)
    try:
        out["tools"] = json.loads(row["tools"] or "[]")
    except Exception:
        out["tools"] = []
    out["enabled"] = bool(row["enabled"])
    return out


def _num(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def ensure_roster():
    """Seed the default roster once, so a fresh install has a working team.

    This runs while the app is being imported, and the app serves with two uvicorn
    workers - so both processes arrive here at the same moment. It used to be a plain
    "count, then insert": on an empty database both read 0, both inserted the whole
    roster, and the deploy came up with every agent twice, which AutoGen then refused
    to build a team from. ``seed_lock`` serialises them; ``INSERT OR IGNORE`` plus the
    unique index on ``agents.name`` mean a repeat cannot duplicate anything even if
    the lock is never taken.
    """
    with get_connection() as conn:
        seed_lock(conn)
        count = conn.execute("SELECT COUNT(*) AS n FROM agents").fetchone()["n"]
        if count:
            return sync_default_tools()
        for item in DEFAULT_ROSTER:
            # OR IGNORE is translated to ON CONFLICT DO NOTHING on PostgreSQL, so an
            # agent that already exists is left exactly as the operator configured it.
            conn.execute(
                "INSERT OR IGNORE INTO agents "
                "(name, role, provider, model, system_prompt, tools, turn_order, enabled) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
                (
                    item["name"],
                    item["role"],
                    item["provider"],
                    item["model"],
                    item["system_prompt"],
                    json.dumps(item["tools"]),
                    item["turn_order"],
                ),
            )
    return sync_default_tools()


def sync_default_tools():
    """Grant existing agents any tools the defaults now assign them.

    Additive only - nothing is removed or renamed - so it is safe to run on every
    startup, and a newly shipped tool appears on existing installs without the
    operator re-ticking boxes in the roster editor.
    """
    defaults = {item["name"]: (item.get("tools") or []) for item in DEFAULT_ROSTER}
    changed = 0
    for agent in list_agents():
        wanted = defaults.get(agent["name"])
        if not wanted:
            continue
        current = agent["tools"] or []
        missing = [tool for tool in wanted if tool not in current]
        if missing:
            update_agent(agent["id"], {"tools": current + missing})
            changed += 1
    return changed


def list_agents(enabled_only=False):
    query = "SELECT * FROM agents"
    if enabled_only:
        query += " WHERE enabled = 1"
    query += " ORDER BY turn_order ASC, id ASC"
    with get_connection() as conn:
        rows = conn.execute(query).fetchall()
    return [_agent_out(r) for r in rows]


def get_agent(agent_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
    return _agent_out(row) if row else None


def _duplicate_message(name):
    return (
        "Another agent is already called %r. AutoGen identifies participants by name, "
        "so a team cannot contain two agents with the same name." % name
    )


def _name_taken(name, exclude_id=None):
    """Is another agent already called this? Compared case-insensitively.

    Case-insensitive on purpose: "Operator" and "operator" are different to AutoGen,
    but they are never different on purpose, and the near-duplicate is invisible in
    the roster list until the team fails.
    """
    query = "SELECT id FROM agents WHERE lower(name) = lower(?)"
    params = [name]
    if exclude_id is not None:
        query += " AND id <> ?"
        params.append(exclude_id)
    with get_connection() as conn:
        return conn.execute(query, tuple(params)).fetchone() is not None


def create_agent(data):
    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("Name is required")
    if _name_taken(name):
        raise ValueError(_duplicate_message(name))
    try:
        with get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO agents (name, role, provider, model, system_prompt, tools, turn_order, enabled, temperature, max_tokens) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    name,
                    (data.get("role") or "").strip(),
                    (data.get("provider") or "deepseek").strip(),
                    (data.get("model") or "").strip(),
                    (data.get("system_prompt") or "").strip(),
                    json.dumps(data.get("tools") or []),
                    int(data.get("turn_order") or 0),
                    1 if data.get("enabled", True) else 0,
                    _num(data.get("temperature")),
                    _num(data.get("max_tokens")),
                ),
            )
            agent_id = cur.lastrowid
    except IntegrityError:
        # The unique index caught a name that appeared between the check and the
        # insert - two people editing the roster at once.
        raise ValueError(_duplicate_message(name))
    return get_agent(agent_id)


def update_agent(agent_id, data):
    if not get_agent(agent_id):
        return None
    candidate = None
    if "name" in data:
        candidate = (data.get("name") or "").strip()
        if not candidate:
            raise ValueError("Name is required")
        if _name_taken(candidate, exclude_id=agent_id):
            raise ValueError(_duplicate_message(candidate))
    sets, values = [], []
    for field in TEXT_FIELDS:
        if field in data:
            value = data.get(field)
            sets.append("%s = ?" % field)
            values.append(value.strip() if isinstance(value, str) else (value or ""))
    if "tools" in data:
        sets.append("tools = ?")
        values.append(json.dumps(data.get("tools") or []))
    if "turn_order" in data:
        sets.append("turn_order = ?")
        values.append(int(data.get("turn_order") or 0))
    if "enabled" in data:
        sets.append("enabled = ?")
        values.append(1 if data.get("enabled") else 0)
    for field in ("temperature", "max_tokens"):
        if field in data:
            sets.append("%s = ?" % field)
            values.append(_num(data.get(field)))
    if not sets:
        return get_agent(agent_id)
    sets.append("updated_at = ?")
    values.append(now_str())
    values.append(agent_id)
    try:
        with get_connection() as conn:
            conn.execute("UPDATE agents SET %s WHERE id = ?" % ", ".join(sets), values)
    except IntegrityError:
        if candidate is None:
            raise  # not the name index - let it surface as the real error
        raise ValueError(_duplicate_message(candidate))
    return get_agent(agent_id)


def delete_agent(agent_id):
    with get_connection() as conn:
        conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
    return True


# ---------------------------------------------------------------------------
# Team execution
# ---------------------------------------------------------------------------
def _safe_name(name):
    """AutoGen agent names must be identifier-like."""
    cleaned = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in (name or "agent"))
    cleaned = cleaned.strip("_") or "agent"
    if cleaned[0].isdigit():
        cleaned = "a" + cleaned
    return cleaned[:64]


def unique_name(name, taken):
    """A name AutoGen accepts: sanitised, and not already used in this team.

    ``_safe_name`` is lossy - "Image Artist" and "Image-Artist" both become
    "Image_Artist" - so two distinct agents can collide *after* sanitising even when
    the roster is clean. AutoGen requires unique participant names and rejects the
    whole team, so disambiguate rather than refuse: the operator's roster is right,
    the identifier just needs to be.
    """
    base = _safe_name(name)
    candidate = base
    suffix_n = 2
    while candidate in taken:
        suffix = "_%d" % suffix_n
        candidate = base[: 64 - len(suffix)] + suffix
        suffix_n += 1
    taken.add(candidate)
    return candidate


# Appended to the instructions of any agent holding save_deliverable. It lives in code
# rather than in DEFAULT_ROSTER's system_prompt because an existing install keeps the
# prompt it was seeded with - sync_default_tools only ever adds tools, never rewrites
# prose - so a rule about a tool has to travel with the tool.
CONTENT_RULE = (
    "\n\nWhen the user asks for content - an email, a blog post, a funnel step or a "
    "website page - finish by calling save_deliverable(kind, title, content) so it "
    "appears in their console. Pass the complete document as content, then summarise "
    "what you made in a sentence or two instead of pasting the whole thing. "
    "Every <img> must use an absolute https URL (the download_url search_photos "
    "returns); a local disk path renders as a broken image for the reader. Never leave "
    "a placeholder such as [INSERT IMAGE URL] or [Your Name] in content the user will "
    "receive - call search_photos yourself and use a real URL, or leave the image out."
)


def system_message_for(row):
    """An agent's instructions, plus the rules that come with the tools it holds."""
    message = row.get("system_prompt") or row.get("role") or "You are a helpful assistant."
    if "save_deliverable" in (row.get("tools") or []):
        message += CONTENT_RULE
    return message


def _event_type(message_type):
    if message_type == "ToolCallRequestEvent":
        return "tool_call"
    if message_type == "ToolCallExecutionEvent":
        return "tool_result"
    if message_type in ("TaskResult", "SelectorEvent"):
        return "system"
    return "message"


def _describe(message):
    """Normalise an AutoGen message into (source, type, text)."""
    source = getattr(message, "source", "") or "team"
    message_type = getattr(message, "type", type(message).__name__)
    content = getattr(message, "content", None)

    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = []
        for item in content:
            inner = getattr(item, "content", None)
            if inner is None:
                inner = getattr(item, "name", None)
            parts.append(str(inner if inner is not None else item))
        text = "\n".join(parts)
    elif content is None:
        text = ""
    else:
        text = str(content)

    stop_reason = getattr(message, "stop_reason", None)
    if stop_reason:
        text = (text + "\n(stop_reason: %s)" % stop_reason).strip()
    return source, message_type, text


def guard_tools_supported(row, model_client, tool_count):
    """Refuse to attach tools to a model that cannot call them - naming the agent.

    AutoGen raises "The model does not support function calling." from deep inside
    ``AssistantAgent``, which says nothing about *which* agent or what to change. The
    usual cause is a model name autogen-ext does not recognise: its registry answers
    ``function_calling=False`` for anything unknown, and a single custom model string
    then fails the whole team build.
    """
    info = getattr(model_client, "model_info", None) or {}
    if info.get("function_calling") is not False:
        return
    raise ValueError(
        "Agent %r (provider %r, model %r) is granted %d tool(s), but that model does "
        "not support function calling - AutoGen does not recognise the model name. "
        "Set a known model on the agent or in Settings, or clear the field to use the "
        "provider's default."
        % (
            row.get("name"),
            row.get("provider"),
            row.get("model") or "(provider default)",
            tool_count,
        )
    )


async def build_team(job, emit, factory):
    """Construct the AutoGen team from the enabled roster (no execution)."""
    from autogen_agentchat.agents import AssistantAgent
    from autogen_agentchat.conditions import MaxMessageTermination, TextMentionTermination
    from autogen_agentchat.teams import RoundRobinGroupChat, SelectorGroupChat

    roster = list_agents(enabled_only=True)
    if not roster:
        raise ValueError("No enabled agents. Add or enable agents in the Agents tab first.")

    max_messages = int(os.environ.get("AGENT_MAX_MESSAGES", "20"))
    participants = []
    taken = set()

    for row in roster:
        granted = row["tools"] or []
        missing = tool_registry.unknown(granted)
        if missing:
            emit("system", "system", "Agent '%s' references unknown tools: %s" % (row["name"], ", ".join(missing)))
        client_kwargs = {}
        if row.get("temperature") is not None:
            client_kwargs["temperature"] = float(row["temperature"])
        model_client = factory(row["provider"], row["model"] or None, **client_kwargs)
        tools = tool_registry.resolve(granted, emit, job_id=job.get("id"))
        if tools:
            guard_tools_supported(row, model_client, len(tools))
        participant_name = unique_name(row["name"], taken)
        if participant_name != _safe_name(row["name"]):
            emit(
                "system",
                "system",
                "Agent '%s' sanitises to a name another agent already uses; this run "
                "calls it '%s'." % (row["name"], participant_name),
            )
        options = {
            "name": participant_name,
            "model_client": model_client,
            "system_message": system_message_for(row),
        }
        if tools:
            options["tools"] = tools
        participants.append(AssistantAgent(**options))

    termination = TextMentionTermination("APPROVE") | MaxMessageTermination(max_messages)
    mode = (job.get("team_mode") or "selector").strip().lower()

    if mode == "round_robin" or len(participants) == 1:
        team = RoundRobinGroupChat(participants, termination_condition=termination)
    else:
        router_provider = (os.environ.get("AGENT_ROUTER_PROVIDER") or "deepseek").strip()
        router_model = (os.environ.get("AGENT_ROUTER_MODEL") or "").strip() or None
        team = SelectorGroupChat(
            participants,
            model_client=factory(router_provider, router_model),
            termination_condition=termination,
        )
    return team, participants


async def run_team(job, emit, model_client_factory=None):
    """Run the roster-built AutoGen team for one job.

    ``model_client_factory`` is injectable so tests can drive the team with
    AutoGen's replay client instead of a live provider.
    """
    factory = model_client_factory or llm.get_model_client
    team, participants = await build_team(job, emit, factory)
    emit("system", "system", "Team: " + ", ".join(p.name for p in participants))

    produced = 0
    async for message in team.run_stream(task=job.get("prompt") or ""):
        source, message_type, text = _describe(message)
        if text:
            emit(source, _event_type(message_type), text)
        produced += 1

    return "agent team finished (%d messages)" % produced


@jobs.task("agent_team")
async def agent_team_task(job, emit):
    """Job task: run the AutoGen team configured in the roster."""
    return await run_team(job, emit)


