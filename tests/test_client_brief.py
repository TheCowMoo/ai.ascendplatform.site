"""The client brief and the preferences the Manager learns by itself.

Run: .\\venv\\Scripts\\python.exe tests\\test_client_brief.py

Before this, the Business Profile reached the chatbot and nothing else: the Manager ran
with a constant system prompt and every roster agent ran with only its own instructions.
So a client could write "we never use exclamation marks" and the next email still had
them. These checks pin the two halves of the fix - a compact brief injected into every
agent turn, and a deduplicated, capped store that `remember_preference` writes to.
"""

import asyncio
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="client-brief-"), "test.db").replace("\\", "/")
os.environ.pop("ALLOW_LIVE_ACTIONS", None)
os.environ.pop("JOB_BACKEND", None)
os.environ.pop("REDIS_URL", None)

import agents  # noqa: E402
import db  # noqa: E402
import jobs  # noqa: E402
import manager  # noqa: E402
import profile as profile_mod  # noqa: E402
import tools  # noqa: E402
import tools.manager as manager_tools  # noqa: E402

db.init_db()

FAILED = []
ORG = 501
OTHER = 502


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


def test_the_brief_is_compact_and_carries_the_rules():
    profile_mod.update_profile(ORG, {
        "name": "Acme Ltd",
        "tagline": "Practical growth.",
        "description": "We help small teams grow. " * 80,
        "target_audience": "Small business owners in the UK.",
        "tone_of_voice": "Direct, warm and jargon-free.",
        "services": [{"name": "Chatbot", "price": "$499"}],
        "preferences": "Keep emails under 120 words.\nNever use exclamation marks.",
        "signature": "Nathanael\nFounder, Acme Ltd",
        "policies": "No refunds after 14 days.",
    })
    brief = profile_mod.brief_for_org(ORG)
    for needle in ("[Client context", "Business: Acme Ltd", "Audience: Small business",
                   "Tone of voice: Direct", "Keep emails under 120 words.",
                   "Never use exclamation marks.", "Sign written work as",
                   "Founder, Acme Ltd", "- Chatbot ($499)"):
        check("the brief carries %r" % needle, needle in brief, True)
    check("it stays inside the cap", len(brief) <= profile_mod.BRIEF_LIMIT, True)
    check("it is not the whole knowledge base", "FAQ" in brief, False)

    capped = profile_mod.build_brief({"name": "X", "description": "a" * 9000,
                                      "preferences": "- " + "b" * 4000}, limit=500)
    check("a long profile is truncated, not appended", len(capped) <= 500, True)
    check("a client with no profile has no brief",
          profile_mod.brief_for_org(999999), "")
    check("no org at all is no brief", profile_mod.brief_for_org(None), "")


def test_learning_saves_once_and_keeps_the_newest():
    saved, message = profile_mod.add_preference(ORG, "Keep emails under 120 words.")
    check("the same rule is not saved twice", saved, False)
    check("and it says so", "Already saved" in message, True)

    saved, message = profile_mod.add_preference(
        ORG, "- Never open with 'I hope this email finds you well'.")
    check("a new rule is saved", saved, True)
    stored = profile_mod.get_profile(ORG)["preferences"]
    check("as one bullet, not two", stored.count("I hope this email finds you well"), 1)
    check("without a double bullet", "--- " in stored, False)
    check("another client is untouched",
          "I hope this email finds you well" in
          (profile_mod.get_profile(OTHER) or {}).get("preferences", ""), False)

    original = profile_mod.MAX_PREFERENCES
    profile_mod.MAX_PREFERENCES = 3
    try:
        for index in range(5):
            profile_mod.add_preference(OTHER, "Rule %d" % index)
    finally:
        profile_mod.MAX_PREFERENCES = original
    lines = [line for line in profile_mod.get_profile(OTHER)["preferences"].splitlines() if line]
    check("only the newest rules survive the cap", lines,
          ["- Rule 2", "- Rule 3", "- Rule 4"])
    check("and the brief still has them", "Rule 4" in profile_mod.brief_for_org(OTHER), True)


def test_every_turn_gets_it():
    brief = profile_mod.brief_for_org(ORG)
    check("the manager's system prompt carries it",
          brief in manager.system_for(ORG), True)
    check("and a turn with no client is just the instructions",
          manager.system_for(None), manager.SYSTEM)
    check("the manager may learn preferences",
          "remember_preference" in manager.GRANTED, True)
    check("the tool is registered",
          tools.unknown(["remember_preference"]), [])

    row = {"name": "Copywriter", "system_prompt": "You write the emails.",
           "tools": []}
    without = agents.system_message_for(row)
    with_brief = agents.system_message_for(row, brief)
    check("an agent without a client is unchanged", "Client context" in without, False)
    check("an agent gets the client brief", brief in with_brief, True)
    check("after its own instructions",
          with_brief.index("You write the emails.") < with_brief.index("[Client context"), True)

    # The helpers above are only useful if the two entry points actually call them.
    manager_source = open(os.path.join(ROOT, "manager.py"), encoding="utf-8").read()
    agents_source = open(os.path.join(ROOT, "agents.py"), encoding="utf-8").read()
    check("the chat task uses the client's system prompt",
          'system_for(job.get("org_id"))' in manager_source, True)
    check("the team hands the brief to every agent",
          "system_message_for(row, client_brief" in agents_source, True)


def test_the_tool_writes_to_the_jobs_client():
    job_id = jobs.create_job("Chat", "hello", None, "selector", "chat", org_id=ORG)
    remember = manager_tools._build_remember_preference(None, job_id=job_id)
    result = asyncio.run(remember("Always sign off as Nathanael."))
    check("the tool confirms what it saved", "Always sign off as Nathanael." in result, True)
    check("and it reached the client's profile",
          "Always sign off as Nathanael." in profile_mod.get_profile(ORG)["preferences"], True)

    orphan = jobs.create_job("Chat", "hello", None, "selector", "chat", org_id=None)
    remember = manager_tools._build_remember_preference(None, job_id=orphan)
    result = asyncio.run(remember("Keep it short."))
    check("a chat with no client refuses to invent one",
          "not attached to a client" in result, True)
    check("and nothing was written",
          "Keep it short." in (profile_mod.get_profile(ORG)["preferences"] or ""), False)


if __name__ == "__main__":
    test_the_brief_is_compact_and_carries_the_rules()
    test_learning_saves_once_and_keeps_the_newest()
    test_every_turn_gets_it()
    test_the_tool_writes_to_the_jobs_client()
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nCLIENT BRIEF OK - every agent turn follows the client's own rules")
