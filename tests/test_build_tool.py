"""The Manager starts the guided interview instead of asking technical questions.

Run: .\\venv\\Scripts\\python.exe tests\\test_build_tool.py
"""

import asyncio
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="build-tool-"), "test.db").replace("\\", "/")
os.environ.pop("ALLOW_LIVE_ACTIONS", None)
os.environ.pop("JOB_BACKEND", None)
os.environ.pop("REDIS_URL", None)

from autogen_core import CancellationToken  # noqa: E402

import builds  # noqa: E402
import db  # noqa: E402
import jobs  # noqa: E402
import manager  # noqa: E402
import orgs as orgs_service  # noqa: E402
import tools  # noqa: E402

db.init_db()
FAILED = []


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


def test_the_tool_starts_a_build_and_emits_it():
    org = orgs_service.create_org({"name": "Tool Build"})["id"]
    job = jobs.create_job("Build", "create a site", None, "selector", "chat", org_id=org)
    seen = []
    tool = tools.resolve(["start_build"],
                         emit=lambda source, kind, content: seen.append((source, kind, content)),
                         job_id=job)[0]
    reply = asyncio.run(tool.run_json(
        {"kind": "website", "goal": "a bakery website"}, CancellationToken()))
    check("the tool says it started", "Started" in reply, True)
    check("it tells the manager not to repeat the questions",
          "do not repeat" in reply.lower(), True)
    event = [content for source, kind, content in seen if kind == "build"][0]
    payload = json.loads(event)
    check("the event names the kind", payload["kind"], "website")
    row = builds._row(payload["build_id"], org)
    check("the build belongs to the client", row["org_id"], org)
    check("the manager may use it", "start_build" in manager.GRANTED, True)


def test_an_unknown_kind_is_refused_without_a_row():
    org = orgs_service.create_org({"name": "Tool Bad"})["id"]
    job = jobs.create_job("Build", "x", None, "selector", "chat", org_id=org)
    tool = tools.resolve(["start_build"], job_id=job)[0]
    before = len(builds.list_for_org(org))
    reply = asyncio.run(tool.run_json({"kind": "spaceship"}, CancellationToken()))
    check("it names the choices", "website" in reply.lower(), True)
    check("nothing was saved", len(builds.list_for_org(org)), before)


if __name__ == "__main__":
    test_the_tool_starts_a_build_and_emits_it()
    test_an_unknown_kind_is_refused_without_a_row()
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nBUILD TOOL OK - the Manager opens a plain-language card")
