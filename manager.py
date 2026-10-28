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
        "Ask before you guess, but only when it matters. If the brief is vague - under about a "
    "dozen words, or missing what the thing is for - call ask_user with at most three "
    "short questions whose answers would change the output: for an email, who it goes to "
    "and what it offers; for a funnel, the entry offer and the steps; for a website, its "
    "purpose and sections; for an automation, its trigger and whether sending needs "
    "approval. If the brief already covers those, do not ask - build it.\n"
    "You cannot send email, publish, or touch servers - those are jobs with approvals, "
    "so delegate them with start_job and say that they await approval. Never claim "
    "something is published or sent.\n"
    "Be brief: one short paragraph or a few bullets. Use the client's numbers when you "
    "have them, and say so when you do not."
)

GRANTED = ("list_sites", "list_deliverables", "get_metrics", "start_job",
           "save_deliverable", "propose_automation", "describe_capabilities",
           "propose_tool", "ask_user")


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
    )
    emit("system", "system", "Manager is thinking…")
    result = await agent.run(task=job.get("prompt") or "")
    messages = getattr(result, "messages", []) or []
    return str(getattr(messages[-1], "content", "") or "") if messages else ""
