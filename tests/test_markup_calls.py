"""A model that writes its tool calls as text still gets its work done.

DeepSeek's chat models sometimes emit "<||DSML||invoke name=...>" into the message *content*
instead of returning structured calls. AutoGen sees prose, nothing runs, and the user gets a
wall of markup (or, when there was no prose at all, "No answer"). These tests pin the parser
that turns that markup back into real calls, and the loop in ``chat_task`` that runs them.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("ALLOW_LIVE_ACTIONS", None)

import jobs
import llm
import manager

BAR = chr(0xFF5C)  # the fullwidth bar DeepSeek uses
FAILED = []


def check(name, got, want):
    if got != want:
        FAILED.append("%s\n   got  %r\n   want %r" % (name, got, want))
        print("FAIL %s -> %r" % (name, got))
    else:
        print("ok   %s" % name)


def markup(call, *params):
    """One invoke block in the shape DeepSeek actually writes."""
    body = " <".join("%s%sDSML%s%s parameter name=\"%s\" string=\"true\">%s</" % (
        BAR, BAR, BAR, BAR, name, value) for name, value in params)
    return ("<%s%sDSML%s%s calls> <%s%sDSML%s%s invoke name=\"%s\"> %s </%s%sDSML%s%s invoke> "
            "</%s%sDSML%s%s calls>" % (BAR, BAR, BAR, BAR, BAR, BAR, BAR, BAR,
                                       call, body, BAR, BAR, BAR, BAR, BAR, BAR, BAR, BAR))


def test_parses_leaked_calls():
    text = "Proposing the graph now. " + markup(
        "create_template", ("name", "Travel welcome"), ("subject", "Hello"),
        ("body", "Thanks for subscribing."))
    check("prose before markup is kept",
          manager._strip_markup(text), "Proposing the graph now.")
    check("call is found", [name for name, _ in manager._markup_calls(text)],
          ["create_template"])
    check("arguments survive", manager._markup_calls(text)[0][1],
          {"name": "Travel welcome", "subject": "Hello", "body": "Thanks for subscribing."})


def test_ascii_pipes_and_json_args():
    text = ('<||DSML|| calls> <||DSML|| invoke name="propose_automation"> '
            '<||DSML|| parameter name="steps" string="true">'
            '[{"id": "a", "type": "delay", "config": {"days": 1}}]'
            '</||DSML|| parameter> </||DSML|| invoke> </||DSML|| calls>')
    calls = manager._markup_calls(text)
    check("ascii bars parse", [name for name, _ in calls], ["propose_automation"])
    check("json value becomes a list", isinstance(calls[0][1]["steps"], list), True)


def test_plain_text_untouched():
    check("no markup, no calls", manager._markup_calls("just talking"), [])
    check("no markup, text intact", manager._strip_markup("just talking"), "just talking")
    check("markup with nothing before it strips to empty",
          manager._strip_markup(markup("list_sites")), "")


def test_chat_task_runs_what_the_model_wrote():
    """The turn that used to say "No answer" now builds the thing and answers in words."""
    text = markup("create_template", ("name", "Travel welcome"),
                  ("subject", "Welcome aboard"),
                  ("body", "Hi {{contact.name}}, thanks for subscribing."))

    class Message:
        def __init__(self, content):
            self.content = content

    class Result:
        def __init__(self, content):
            self.messages = [Message(content)]

    class FakeAgent:
        """First turn leaks markup, second turn answers - exactly what DeepSeek did."""

        def __init__(self, *args, **kwargs):
            self.turns = 0

        async def run(self, task=""):
            self.turns += 1
            if self.turns == 1:
                return Result("Writing the email. " + text)
            return Result("Built template. SMTP is not configured, so sends will fail.")

    import autogen_agentchat.agents as agents_module

    agents_module.AssistantAgent = FakeAgent
    llm.get_model_client = lambda provider: None
    job_id = jobs.create_job("chat", "make a sequence", None, "selector", "chat")
    events = []
    answer = asyncio.run(manager.chat_task(
        {"id": job_id, "prompt": "make a sequence", "provider": "deepseek"},
        lambda *args: events.append(args)))
    said = [str(event[2]) for event in events]
    check("the leaked call ran", "create_template" in said, True)
    check("the fact it was text is stated",
          any("wrote as text" in line for line in said), True)
    check("answer is prose, not markup", answer,
          "Built template. SMTP is not configured, so sends will fail.")


if __name__ == "__main__":
    test_parses_leaked_calls()
    test_ascii_pipes_and_json_args()
    test_plain_text_untouched()
    test_chat_task_runs_what_the_model_wrote()
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nMARKUP CALLS OK - leaked tool calls are executed, and every turn answers")
