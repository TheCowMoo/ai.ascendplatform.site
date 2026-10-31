"""The floating manager: one agent that answers, edits drafts and delegates.

Deliberately a single agent rather than the full roster - a chat reply should take a few
seconds, not a team conference. It can start team jobs, which is where real work goes.
"""

SYSTEM = (
    "You are the platform's general manager. Answer from your tools, never invent "
    "numbers or statuses. You can list sites and drafts, read traffic, edit a draft, and "
    "start jobs for the agent team.\n"
    "You can build automations. Call describe_capabilities first to see this client's "
    "triggers, tools, conditions and templates, then call propose_automation with a "
    "complete JSON graph. A proposal is not live: the user previews it and presses Apply. "
    "If the graph is refused the validator's complaints come back - fix them and call "
    "again with the whole graph.\n"
        "Create what the work needs. If a sequence needs an email, write the template with "
    "create_template and use its id in the send step - do not tell the user to go and make "
    "one. Call list_templates first so you reuse an existing one instead of duplicating it. "
    "For images: the client's own Media is the first place to look; call list_media and "
    "use its URL and alt text. If Media is empty or has no suitable shot, do not stop - "
    "call search_photos for stock photography, then import_photo_to_media to download the "
    "chosen photo into this client's Media. Use the local media URL/HTML, alt text and "
    "credit line it returns; never paste a remote Pexels URL or a local disk path into content. "
    "Searching and importing stock photos are internal and need no approval. "
    "The only things you may not create are credentials and domains: SMTP settings, API "
    "keys, and the domain a site lives on are the user's to give. If one of those is "
    "missing, say exactly which setting and why, once, at the end.\n"
    "When the prompt starts with [Studio context ...] naming an existing deliverable, you "
    "are editing that draft, not writing a new one: call save_deliverable with "
    "deliverable_id=<that id> and the complete updated document. Never save a second "
    "draft unless the user explicitly asks for a different page. If you hand the edit to "
    "the team with start_job, pass deliverable_id too, or the team will save a duplicate "
    "and leave the page the user is looking at unchanged.\n"
    "Ask before you guess, but only when it matters. If the brief is vague - under about a "
    "dozen words, or missing what the thing is for - call ask_user with at most three "
    "short questions whose answers would change the output: for an email, who it goes to "
    "and what it offers; for a funnel, the entry offer and the steps; for a website, its "
    "purpose and sections; for an automation, its trigger and whether sending needs "
    "approval. If the brief already covers those, do not ask - build it.\n"
    "You cannot send email, publish, or touch servers - those are jobs with approvals, "
    "so delegate them with start_job and say that they await approval. Never claim "
    "something is published or sent.\n"
    "Report what blocks the work. describe_capabilities tells you whether email is "
    "configured and which templates exist; if templates is empty or email_ready is false, "
    "say so plainly before or alongside any draft, because a send step cannot work yet. "
    "Always end with a sentence: never finish a turn having only called a tool.\n"
    "Be brief: one short paragraph or a few bullets. Use the client's numbers when you "
    "have them, and say so when you do not."
)

GRANTED = ("list_sites", "list_deliverables", "get_metrics", "start_job",
           "save_deliverable", "list_media", "search_photos", "import_photo_to_media",
           "propose_automation", "describe_capabilities", "propose_tool", "ask_user",
           "create_template", "list_templates", "create_contact", "create_site",
           "create_form", "list_forms")


import json
import re

import jobs

# Where a model's tool-call markup starts. DeepSeek writes "<||DSML||invoke name=...>"; the
# bars turn up as ASCII pipes or fullwidth ones depending on the tokenizer, so match both,
# along with the other shapes seen in the wild.
_INVOKE = re.compile(r"invoke\s+name\s*=\s*[\"']([A-Za-z_]\w*)[\"']")
_PARAM = re.compile(
    r"parameter\s+name\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)(?=parameter\s+name\s*=|</|\Z)",
    re.S)


def _markup_start(text):
    """Where a leaked tool call begins, or -1.

    Deliberately not anchored on the exact marker: the ｜ bars in DeepSeek's
    "<||DSML||invoke name=...>" arrive as ASCII pipes, fullwidth bars, or mangled bytes
    depending on the proxy and the console. What is stable is the shape - a word like DSML,
    or "invoke name=" followed by "parameter name=" - so that is what is matched.
    """
    low = text.lower()
    hits = []
    for token in ("dsml", "antml:invoke", "tool_calls"):
        index = low.find(token)
        if index != -1:
            hits.append(index)
    for hit in _INVOKE.finditer(text):
        if "parameter" in text[hit.end():hit.end() + 400].lower():
            hits.append(hit.start())
            break
    return min(hits) if hits else -1


def _markup_calls(text):
    """[(tool name, args)] for tool calls a model *wrote* instead of sending.

    Nothing here trusts the markup's round-trip fidelity: names are validated by the
    registry (`tools.resolve` drops unknown ones) and values are decoded as JSON where they
    look like JSON, which is how a `steps` list arrives as a list and not as a string.
    """
    if not isinstance(text, str):
        return []
    start = _markup_start(text)
    if start < 0:
        return []
    body = text[start:]
    found = []
    hits = list(_INVOKE.finditer(body))
    for index, hit in enumerate(hits):
        end = hits[index + 1].start() if index + 1 < len(hits) else len(body)
        block = body[hit.end():end]
        args = {}
        for name, raw in _PARAM.findall(block):
            value = raw.strip().lstrip("<|｜?").strip()
            if value[:1] in "[{":
                try:
                    value = json.loads(value)
                except ValueError:
                    pass
            elif value in ("true", "false"):
                value = value == "true"
            args[name.strip()] = value
        found.append((hit.group(1), args))
    return found


def _strip_markup(text):
    """The prose a model wrote, with the markup it leaked after it removed."""
    if not isinstance(text, str):
        return ""
    start = _markup_start(text)
    return (text[:start] if start >= 0 else text).strip().rstrip("|｜?<").strip()



def _provider_for(job):
    """Which provider answers this turn.

    DeepSeek stays the default whenever it is configured, so a deployment that works
    today keeps working. A local install with only a Gemini key used to fail every chat
    with "Provider 'deepseek' is not configured" - the assistant now uses the first
    provider that is actually configured instead of going silent.
    """
    explicit = str(job.get("provider") or "").strip()
    if explicit:
        return explicit

    import llm

    configured = llm.configured_providers()
    if "deepseek" in configured or not configured:
        return llm.DEFAULT_PROVIDER
    return configured[0]


@jobs.task("chat")
async def chat_task(job, emit):
    """One manager turn. Registered as task ``chat``."""
    from autogen_agentchat.agents import AssistantAgent

    import llm
    import tools as tool_registry

    provider = _provider_for(job)
    if provider != llm.DEFAULT_PROVIDER:
        # Say it in the transcript: a fallback that cannot be seen is indistinguishable
        # from the configured model having answered.
        emit("system", "system",
             "DeepSeek is not configured; using %s for this turn." % provider)
    tools = tool_registry.resolve(list(GRANTED), emit, job_id=job.get("id"))
    agent = AssistantAgent(
        name="Manager",
        model_client=llm.get_model_client(provider),
        system_message=SYSTEM,
        tools=tools,
        # Without this AutoGen returns the tool's result and stops, so a turn that needed
        # two calls (capabilities, then a draft) ended in silence - the job "finished" with
        # no message and looked broken. Six calls is enough for describe -> propose.
        reflect_on_tool_use=True,
        max_tool_iterations=6,
    )
    emit("system", "system", "Manager is thinking…")
    result = await agent.run(task=job.get("prompt") or "")
    messages = list(getattr(result, "messages", []) or [])
    said = _answer_from(messages)
    for message in messages:
        for call in _tool_calls(message):
            emit("Manager", "tool", call)
    ran = []

    # Some models (DeepSeek's chat models in particular) print their tool calls as *text* -
    # "<||DSML||invoke name=\"propose_automation\">..." - instead of returning structured
    # calls. AutoGen sees ordinary content, so nothing runs, the user gets a wall of markup,
    # and the turn looks broken. Parse the markup, run it through the same registry, and
    # hand the results back so the model can answer in words.
    for _ in range(3):
        calls = _markup_calls(said)
        if not calls:
            break
        notes = []
        for name, args in calls:
            ran.append(name)
            emit("Manager", "tool", name)
            notes.append("%s -> %s" % (name, await _run_named(name, args, emit, job.get("id"))))
        emit("system", "system",
             "Ran %d call(s) the model wrote as text instead of sending." % len(calls))
        result = await agent.run(task=_follow_up(notes))
        messages = list(getattr(result, "messages", []) or [])
        said = _answer_from(messages)

    prose = _strip_markup(said).strip()
    if prose:
        answer = prose
    elif ran:
        answer = ("I ran %s. Open the job transcript to see the results, and tell me what "
                  "you want done next." % ", ".join(sorted(set(ran))))
    else:
        answer = "I finished without saying anything - open the job transcript to see what ran."
    # Every caller - the Assistant page, the Manager page, the floating sheet - reads
    # Manager/message events. Returning the answer only as jobs.result made a
    # successful turn look like "No answer".
    emit("Manager", "message", answer)
    try:
        import assistant_sessions

        assistant_sessions.record_reply_for_job(job.get("id"), answer)
    except Exception as exc:
        emit("system", "error",
             "Could not save this turn to the Assistant session: %s" % exc)
    return answer


def _follow_up(notes):
    """The prompt that turns markup into work: here is what you meant, keep going."""
    return ("You wrote those tool calls as text instead of calling them, so I ran them for "
            "you:\n%s\n\nNow reply to the user in 1-3 sentences saying what you built and "
            "what is still missing, or call the next tool you need. Never write tool markup "
            "into your reply." % "\n".join(notes))


async def _run_named(name, args, emit, job_id):
    """Run one tool by name with dict arguments, and turn any failure into a sentence."""
    from autogen_core import CancellationToken

    import tools as tool_registry

    resolved = tool_registry.resolve([name], emit, job_id=job_id)
    if not resolved:
        return "no such tool"
    try:
        out = await resolved[0].run_json(args, CancellationToken())
    except Exception as exc:
        return "error: %s" % str(exc)[:300]
    return str(out)[:1500]



def _tool_calls(message):
    """Names of the functions a message asked to run, if any."""
    content = getattr(message, "content", "")
    if "FunctionCall" not in type(message).__name__ or not isinstance(content, list):
        return []
    return [getattr(call, "name", "") or "" for call in content if getattr(call, "name", "")]


def _answer_from(messages):
    """The last thing the manager actually *said*, never an empty string.

    With tool use the final message is often a function result rather than text; returning
    that empty content is what made a completed chat look like a silent failure.
    """
    said = ""
    ran = []
    for message in messages or []:
        content = getattr(message, "content", "")
        ran.extend(_tool_calls(message))
        if isinstance(content, str) and content.strip():
            said = content
    if said:
        return said
    if ran:
        return ("I ran %s but did not write an answer. Tell me what you want done with the "
                "result and I will do it." % ", ".join(sorted(set(ran))))
    return "I finished without saying anything - open the job transcript to see what ran."
