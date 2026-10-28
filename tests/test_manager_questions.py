"""Question parsing: a model's three questions must become three inputs.

Run: .\\venv\\Scripts\\python.exe tests\\test_manager_questions.py

The bug this pins: ask_user emitted one array element containing three numbered questions
separated by newlines, so the console showed a single unusable box. The agent was right and
the tool was wrong, which is the kind of failure that looks like "nothing happened" from the
outside.
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.pop("ALLOW_LIVE_ACTIONS", None)

from autogen_core import CancellationToken  # noqa: E402

import jobs  # noqa: E402
import tools  # noqa: E402


def ask(payload):
    """(reply, questions) for one ask_user call, with a collector standing in for emit."""
    seen = []
    job_id = jobs.create_job("question probe", "x", None, "selector", "echo", None)
    tool = tools.resolve(["ask_user"], emit=lambda src, kind, content: seen.append(kind),
                         job_id=job_id)[0]
    reply = asyncio.run(tool.run_json({"questions": payload}, CancellationToken()))
    questions = []
    if seen:
        # the emit above only records the type; re-run the parse for the payload
        questions = json.loads(_last_payload(payload))
    return reply, questions


def _last_payload(payload):
    seen = []
    job_id = jobs.create_job("question probe", "x", None, "selector", "echo", None)
    tool = tools.resolve(
        ["ask_user"], emit=lambda src, kind, content: seen.append(content),
        job_id=job_id)[0]
    asyncio.run(tool.run_json({"questions": payload}, CancellationToken()))
    return seen[0] if seen else "[]"


def test_numbered_lines_become_separate_questions():
    payload = ('["1) What does the newsletter promise?\\n2) How many emails and over what '
               'span?\\n3) Automatic, or wait for approval?"]')
    reply, questions = ask(payload)
    assert len(questions) == 3, questions
    assert questions[0].startswith("What does the newsletter"), questions
    assert "How many emails" in questions[1], questions
    assert "approval" in questions[2], questions
    assert "3 question" in reply, reply
    print("numbered string -> 3 questions")


def test_a_plain_json_list_is_respected():
    reply, questions = ask('["Who is this going to?", "What is the offer?"]')
    assert questions == ["Who is this going to?", "What is the offer?"], questions
    print("json list -> kept as sent")


def test_at_most_three_questions_survive():
    reply, questions = ask('["one?", "two?", "three?", "four?", "five?"]')
    assert len(questions) == 3, questions
    print("cap holds at 3")


def test_an_empty_ask_says_so():
    reply, questions = ask("[]")
    assert not questions, questions
    assert "nothing was asked" in reply.lower(), reply
    print("empty -> refused")


def main():
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print("ok", test.__name__)
    print("\nMANAGER QUESTIONS OK - one question, one input")


if __name__ == "__main__":
    main()
