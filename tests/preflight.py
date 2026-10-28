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
    "test_manager_questions",
    "test_markup_calls",
    "test_graph_canvas",
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
    assert callable(scheduler.verify_pending), "the DNS sweep is what makes SSL automatic"
    assert callable(automations.validate_graph)
    print("imports OK | tasks:", sorted(jobs.TASKS))


def check_worker_tasks():
    """The worker must register every task the app can create.

    This is the bug that broke automations in production: `chat`, `automation` and
    `site_review` were registered at the bottom of app.py, which the worker never imports,
    so JOB_BACKEND=redis meant the worker refused all three with "Unknown task". Every
    local test passed because they import app. So the check runs in a subprocess that
    imports *only* what worker.py imports - the same isolation the bug needed to hide in.
    """
    if not shutil.which("git") and os.name != "nt":
        pass    # keep going: this check needs no git
    expected = {"agent_team", "automation", "chat", "echo", "site_provision", "site_review"}
    code = (
        "import json, os\n"
        "os.environ.pop('ALLOW_LIVE_ACTIONS', None)\n"
        "import worker\n"
        "import jobs\n"
        "print(json.dumps(sorted(jobs.TASKS)))\n"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError("worker.py failed to import:\n" + (result.stderr or "")[-800:])
    import json as _json

    have = set(_json.loads(result.stdout.strip() or "[]"))
    missing = sorted(expected - have)
    assert not missing, ("the worker cannot run %s - register the task in its own module and "
                         "import that module from worker.py" % ", ".join(missing))
    print("worker registers:", ", ".join(sorted(have)))


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
    # Without a build a deploy recreates containers from the *previous* image: it looks
    # successful and ships nothing, which is exactly how a tab went missing for a day.
    assert "docker compose build" in body, "deploy.sh must rebuild - a recreate reuses the old image"
    assert "APP_VERSION" in body, "deploy.sh must label the build"
    # `--progress` is a `build` flag; compose rejects it on `up`, and that file write is
    # the only thing standing between a working deploy and "unknown flag: --progress".
    up_lines = [line for line in body.splitlines()
                if "docker compose up" in line and not line.strip().startswith("#")]
    for line in up_lines:
        assert "--build" not in line and "--progress" not in line, (
            "deploy.sh passes a build flag to `up` (%s) - build first, then up" % line.strip())
    if shutil.which("git"):
        listed = subprocess.run(["git", "ls-files", "-s", "deploy.sh"], cwd=ROOT,
                                capture_output=True, text=True).stdout.strip()
        if listed:
            mode = listed.split()[0]
            assert mode == "100755", (
                "deploy.sh is committed as %s, so it lands non-executable on the server "
                "(fix: git update-index --chmod=+x deploy.sh)" % mode)
    print("deploy.sh rebuilds, labels the build and rolls back")


def check_icons():
    """Every icon the console asks for must exist in the sprite.

    A missing <symbol> renders an empty SVG - no error, no console warning, just a blank
    gap where the glyph should be. Analytics lost its icon that way and it took someone
    looking at the screen to notice.
    """
    js_path = os.path.join(ROOT, "admin", "admin.js")
    html_path = os.path.join(ROOT, "admin", "index.html")
    if not (os.path.exists(js_path) and os.path.exists(html_path)):
        return
    import re

    with open(js_path, encoding="utf-8") as handle:
        script = handle.read()
    with open(html_path, encoding="utf-8") as handle:
        page = handle.read()
    used = set(re.findall(r"icon: '([a-z0-9-]+)'", script))
    used |= set(re.findall(r"icon\('([a-z0-9-]+)'\)", script))
    have = set(re.findall(r'id="i-([a-z0-9-]+)"', page))
    missing = sorted(used - have)
    assert not missing, "no sprite symbol for: %s (add <symbol id=\"i-..\">)" % ", ".join(missing)
    print("icons: %d used, all present" % len(used))


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
    check_icons()
    check_worker_tasks()
    check_deploy_script()
    try:
        check_js()
    except FileNotFoundError:
        print("admin.js skipped (node not installed)")
    check_suites()
    print("\nPREFLIGHT OK - safe to push")


if __name__ == "__main__":
    main()
