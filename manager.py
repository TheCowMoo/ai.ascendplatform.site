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
    "The only things you may not create are credentials and domains: SMTP settings, API "
    "keys, and the domain a site lives on are the user's to give. If one of those is "
    "missing, say exactly which setting and why, once, at the end.\n"
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
           "save_deliverable", "propose_automation", "describe_capabilities",
           "propose_tool", "ask_user", "create_template", "list_templates")


import jobs


@jobs.task("chat")
async def chat_task(job, emit):
    """One manager turn. Registered as task ``chat``."""
    from autogen_agentchat.agents import AssistantAgent

    import llm
    import tools as tool_registry

    provider = (job.get("provider") or "deepseek").strip()
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
    messages = getattr(result, "messages", []) or []

    for message in messages:
        for call in _tool_calls(message):
            emit("Manager", "tool", call)
    return _answer_from(messages)


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
