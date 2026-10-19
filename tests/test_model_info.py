"""Tool calling must survive any model name (regression for the team-build failure).

Run: .\\venv\\Scripts\\python.exe tests\\test_model_info.py

``AssistantAgent`` refuses to attach tools when its model client reports
``function_calling: False``. The Anthropic client used to take that flag from
autogen-ext's model registry, which answers ``False`` for *any* model name it does
not recognise - so a single custom Claude model string failed the entire team build
with "The model does not support function calling." and no hint of which agent.

This checks the invariant directly, without needing a provider key or a network call.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Constructing a client needs a key but makes no request.
for _name in ("ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "GEMINI_API_KEY"):
    os.environ.setdefault(_name, "test-key-not-a-real-credential")

import agents  # noqa: E402
import llm  # noqa: E402

UNKNOWN = "totally-unknown-model-xyz"


def test_every_provider_reports_tool_support():
    checked = []
    for provider, spec in llm.PROVIDERS.items():
        if not spec["tools"]:
            continue
        client = llm.get_model_client(provider)
        assert client.model_info["function_calling"] is True, (
            provider, dict(client.model_info))
        checked.append(provider)
    assert checked, "no provider declares tool support - the check would be vacuous"
    print("tool support declared and reported:", ", ".join(checked))


def test_an_unrecognised_model_still_allows_tools():
    """The exact failure mode: a model name autogen-ext has never heard of."""
    for provider, spec in llm.PROVIDERS.items():
        if not spec["tools"]:
            continue
        client = llm.get_model_client(provider, UNKNOWN)
        assert client.model_info["function_calling"] is True, (
            provider, dict(client.model_info))
    print("an unrecognised model name still allows tools on every provider")


def test_the_guard_names_the_agent():
    """A refusal has to say *which* agent, or it cannot be acted on."""
    row = {"name": "Operator", "provider": "anthropic", "model": "claude-xyz"}

    class Blocked:
        model_info = {"function_calling": False}

    try:
        agents.guard_tools_supported(row, Blocked(), 10)
    except ValueError as exc:
        message = str(exc)
        assert "Operator" in message, message
        assert "claude-xyz" in message, message
        assert "10 tool" in message, message
        print("refused ->", message[:88] + "...")
    else:
        raise AssertionError("the guard accepted a model that cannot call tools")

    class Capable:
        model_info = {"function_calling": True}

    class Silent:
        pass

    # A capable client passes, and so does one that says nothing: absence of model
    # metadata is not evidence of a problem.
    assert agents.guard_tools_supported(row, Capable(), 10) is None
    assert agents.guard_tools_supported(row, Silent(), 10) is None
    print("allowed -> capable clients and clients with no metadata")


def test_the_agent_that_used_to_fail_now_builds():
    """The real assertion: the AssistantAgent constructor that raised line 774.

    ``_assistant_agent.py`` refuses ``tools`` when ``model_info["function_calling"]``
    is False, which is what killed the team build. This builds one with a model name
    autogen-ext does not know, exactly as the roster did.
    """
    from autogen_agentchat.agents import AssistantAgent

    def echo(text: str) -> str:
        """Return the text unchanged."""
        return text

    client = llm.get_model_client("anthropic", UNKNOWN)
    agent = AssistantAgent(
        name="Operator",
        model_client=client,
        system_message="You are the operator.",
        tools=[echo],
    )
    assert agent.name == "Operator"
    print("AssistantAgent built with a custom model and 1 tool")


def test_empty_text_blocks_are_dropped():
    """Anthropic 400s on an empty text block; AutoGen can produce one.

    An assistant turn that held only tool calls is serialised with a text block of
    "", which kills the run mid-flight ("messages: text content blocks must be
    non-empty"). The Claude client strips it before the request is built.
    """
    from autogen_core.models import AssistantMessage, UserMessage

    messages = [
        UserMessage(content="hello", source="user"),
        AssistantMessage(content="", source="Planner"),
        AssistantMessage(content="   ", source="Planner"),
    ]
    cleaned = llm._clean_messages(messages)
    assert len(cleaned) == 1, cleaned
    assert cleaned[0].content == "hello", cleaned[0]
    print("empty text blocks dropped:", len(messages), "->", len(cleaned))


def main():
    test_every_provider_reports_tool_support()
    test_an_unrecognised_model_still_allows_tools()
    test_the_guard_names_the_agent()
    test_the_agent_that_used_to_fail_now_builds()
    test_empty_text_blocks_are_dropped()
    print("\nMODEL INFO OK")


if __name__ == "__main__":
    main()
