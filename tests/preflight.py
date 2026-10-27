"""Run before every push: does the app still import, and do the fast suites pass?

Run: .\\venv\\Scripts\\python.exe tests\\preflight.py

This exists because three commits in a row broke startup the same way - an edit used an
anchor line as its old_text and forgot to re-emit it, silently deleting a function or an
import. The suites caught it *after* the push each time. Importing the app takes a
second and catches exactly that class of mistake.
"""

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAST_SUITES = (
    "test_model_info",
    "test_db_dialect",
    "test_migrations",
    "test_automation_graph",
    "smoke_automations",
    "smoke_analytics",
    "smoke_deliverables",
    "smoke_workspace",
    "smoke_domains",
    "audit_ui",
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
    import automations

    assert "site_review" in jobs.TASKS, jobs.TASKS
    assert "proposal" in deliverables.KINDS, deliverables.KINDS
    assert callable(scheduler.queue_reviews)
    assert callable(automations.validate_graph)
    print("imports OK | tasks:", sorted(jobs.TASKS))


def check_compose():
    """The worker runs provisioning, so it needs the same site paths as the app.

    This is not pedantry: when only `app` had those mounts, a vhost was written into the
    worker container's own filesystem, Caddy never imported it, and the site reported
    success while nothing went live.
    """
    path = os.path.join(ROOT, "docker-compose.yml")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    worker = text.split("worker:", 1)[-1].split("\n  caddy:", 1)[0]
    missing = [frag for frag in ("/etc/caddy/sites", "/srv/sites", "/app/Caddyfile")
               if frag not in worker]
    if missing:
        raise AssertionError(
            "the worker service is missing %s - provisioning would write where Caddy "
            "cannot see it" % ", ".join(missing))
    # A *directory* bind over /etc/caddy takes the Caddyfile with it: if that directory is
    # absent when the container is created, Caddy starts with no config and exits, and the
    # site is simply unreachable. Mount the file.
    for line in text.splitlines():
        stripped = line.strip().lstrip("- ").strip()
        if stripped.endswith(":/etc/caddy:ro") or stripped.endswith(":/etc/caddy"):
            raise AssertionError(
                "docker-compose.yml mounts a directory over /etc/caddy (%s) - if it is "
                "ever missing, Caddy starts with no config and the site goes down. Mount "
                "./Caddyfile:/etc/caddy/Caddyfile instead." % stripped)
    print("worker shares the site paths with caddy")


def check_caddyfile():
    """Formatting traps Caddy warns about on every reload, caught before they ship.

    `caddy fmt` reports space indentation and CRLF line endings the same way - "input is
    not formatted" plus a line number - so both are checked here rather than guessed at
    while staring at a reload log.
    """
    path = os.path.join(ROOT, "Caddyfile")
    if not os.path.exists(path):
        return
    with open(path, "rb") as handle:
        raw = handle.read()
    assert b"\r" not in raw, "Caddyfile has CRLF line endings - `caddy fmt` will warn"
    assert raw.endswith(b"\n"), "Caddyfile must end with a newline"
    assert b"header_up X-Forwarded-Proto" not in raw, "Caddy sets X-Forwarded-Proto itself"
    indented = [line for line in raw.decode("utf-8").splitlines()
                if line.startswith(" ") and line.strip()]
    assert not indented, "Caddyfile indents with spaces (use tabs): %r" % indented[0]
    print("Caddyfile is clean")


def check_deploy_script():
    """A deploy that cannot prove the site answers is a deploy that can take it down."""
    path = os.path.join(ROOT, "deploy.sh")
    assert os.path.exists(path), "deploy.sh is missing - nothing verifies a deploy"
    with open(path, encoding="utf-8") as handle:
        body = handle.read()
    assert "api/health" in body, "deploy.sh must check the site answers through Caddy"
    assert "git revert" in body, "deploy.sh must roll back when the check fails"
    # Without --build a deploy recreates containers from the *previous* image: it looks
    # successful and ships nothing, which is exactly how a tab went missing for a day.
    assert "--build" in body, "deploy.sh must rebuild - a recreate reuses the old image"
    assert "APP_VERSION" in body, "deploy.sh must label the build"
    if shutil.which("git"):
        listed = subprocess.run(["git", "ls-files", "-s", "deploy.sh"], cwd=ROOT,
                                capture_output=True, text=True).stdout.strip()
        if listed:
            mode = listed.split()[0]
            assert mode == "100755", (
                "deploy.sh is committed as %s, so it lands non-executable on the server "
                "(fix: git update-index --chmod=+x deploy.sh)" % mode)
    print("deploy.sh rebuilds, labels the build and rolls back")


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
    check_compose()
    check_caddyfile()
    check_deploy_script()
    try:
        check_js()
    except FileNotFoundError:
        print("admin.js skipped (node not installed)")
    check_suites()
    print("\nPREFLIGHT OK - safe to push")


if __name__ == "__main__":
    main()
