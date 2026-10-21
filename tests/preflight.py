"""Run before every push: does the app still import, and do the fast suites pass?

Run: .\\venv\\Scripts\\python.exe tests\\preflight.py

This exists because three commits in a row broke startup the same way - an edit used an
anchor line as its old_text and forgot to re-emit it, silently deleting a function or an
import. The suites caught it *after* the push each time. Importing the app takes a
second and catches exactly that class of mistake.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAST_SUITES = (
    "test_model_info",
    "test_db_dialect",
    "smoke_analytics",
    "smoke_deliverables",
    "smoke_review",
    "smoke_asgi",
)


def check_imports():
    import jobs

    import app  # noqa: F401  - the whole point: building the app must not raise
    import analytics  # noqa: F401
    import deliverables
    import scheduler
    import review  # noqa: F401

    assert "site_review" in jobs.TASKS, jobs.TASKS
    assert "proposal" in deliverables.KINDS, deliverables.KINDS
    assert callable(scheduler.queue_reviews)
    print("imports OK | tasks:", sorted(jobs.TASKS))


def check_js():
    if not os.path.exists(os.path.join(ROOT, "admin", "admin.js")):
        return
    result = subprocess.run(
        ["node", "--check", os.path.join(ROOT, "admin", "admin.js")],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise AssertionError("admin.js does not parse:\n" + (result.stderr or "")[:600])
    print("admin.js parses")


def check_suites():
    failures = []
    for name in FAST_SUITES:
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "tests", name + ".py")],
            cwd=ROOT, capture_output=True, text=True,
        )
        status = "ok" if result.returncode == 0 else "FAILED"
        print("  %-22s %s" % (name, status))
        if result.returncode != 0:
            failures.append((name, (result.stdout or "") + (result.stderr or "")))
    if failures:
        for name, output in failures:
            print("\n--- %s ---\n%s" % (name, output[-1500:]))
        raise SystemExit(1)


def main():
    check_imports()
    try:
        check_js()
    except FileNotFoundError:
        print("admin.js skipped (node not installed)")
    check_suites()
    print("\nPREFLIGHT OK - safe to push")


if __name__ == "__main__":
    main()
