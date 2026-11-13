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
    "test_job_enqueue",
    "test_usage",
    "test_markup_calls",
    "test_graph_canvas",
    "test_form_logic",
    "smoke_forms",
    "smoke_media",
    "smoke_stock_media",
    "test_artifact_paths",
    "smoke_assistant_sessions",
    "test_chat_thread",
    "test_dns_records",
    "test_auto_provision",
    "test_provision_approval",
    "test_caddy_reload",
    "smoke_provisioner",
    "smoke_profile",
    "test_client_brief",
    "test_read_only_jobs",
    "test_publish_live",
    "test_domain_removal",
    "test_publish_home",
    "test_publish_tool",
    "test_fetch_page",
    "test_site_extras",
    "test_form_hosting",
    "test_build_guides",
    "test_build_service",
    "test_build_api",
    "test_build_tool",
    "test_client_ghl",
    "smoke_onboarding",
    "smoke_voice",
    "smoke_automations",
    "smoke_analytics",
    "smoke_deliverables",
    "smoke_studio",
    "smoke_workspace",
    "smoke_domains",
    "smoke_usage_api",
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
    import build_service  # noqa: F401

    assert "site_review" in jobs.TASKS, jobs.TASKS
    assert "build" in jobs.TASKS, jobs.TASKS
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
    expected = {"agent_team", "automation", "build", "chat", "echo", "site_provision",
                "site_review"}
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
    # The reload cannot be a shell command: this image has no caddy binary, and the shell
    # policy refuses curl. Both app and worker must be told where the admin API answers.
    app = text.split("\n  app:", 1)[-1].split("\n  worker:", 1)[0]
    for name, service in (("app", app), ("worker", worker)):
        assert "CADDY_ADMIN_URL" in service, (
            "the %s service has no CADDY_ADMIN_URL - its reload would fall back to a "
            "command that either does not exist or is refused by policy" % name)
    assert "curl -fsS -X POST" not in text, (
        "docker-compose.yml still configures a curl reload - the command policy denies "
        "curl, so every live provisioning run would fail at the reload")
    caddy = text.split("\n  caddy:", 1)[-1]
    assert "ACME_EMAIL" in caddy, (
        "the caddy service does not receive ACME_EMAIL - it falls back to ops@example.com, "
        "which Let's Encrypt refuses (invalidContact) and then quietly switches to ZeroSSL")
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
    # A contact at example.com is refused by Let's Encrypt (invalidContact). Caddy then
    # falls back to ZeroSSL, so the site works and every certificate quietly comes from
    # the wrong CA - the failure is only visible in the log.
    assert b"ops@example.com" not in raw, (
        "Caddyfile asks Let's Encrypt for a contact at example.com, which it refuses; "
        "Caddy would silently issue from ZeroSSL instead")
    assert b"ACME_EMAIL" in raw, "Caddyfile must take its ACME contact from ACME_EMAIL"
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
    paths = [os.path.join(ROOT, "admin", "admin.js"),
             os.path.join(ROOT, "admin", "voice.js")]
    paths = [path for path in paths if os.path.exists(path)]
    if not paths:
        return
    for path in paths:
        result = subprocess.run(["node", "--check", path], capture_output=True, text=True)
        if result.returncode != 0:
            raise AssertionError("%s does not parse:\n%s"
                                 % (os.path.basename(path), (result.stderr or "")[:600]))
    print("%s parse" % " and ".join(os.path.basename(path) for path in paths))

    harness = os.path.join(ROOT, "tests", "voice_ui.mjs")
    if os.path.exists(harness):
        result = subprocess.run(["node", harness], capture_output=True, text=True)
        if result.returncode != 0:
            raise AssertionError("the voice overlay harness failed:\n%s"
                                 % ((result.stdout or "") + (result.stderr or ""))[:1200])
        print("voice overlay harness OK")

    # The credit bar's math is the same kind of invisible: a wrong width is still a bar.
    harness = os.path.join(ROOT, "tests", "credit_bar.mjs")
    if os.path.exists(harness):
        result = subprocess.run(["node", harness], capture_output=True, text=True)
        if result.returncode != 0:
            raise AssertionError("the credit bar harness failed:\n%s"
                                 % ((result.stdout or "") + (result.stderr or ""))[:1200])
        print("credit bar harness OK")

    # The remembered draft is invisible until a voice edits the wrong page.
    harness = os.path.join(ROOT, "tests", "studio_draft.mjs")
    if os.path.exists(harness):
        result = subprocess.run(["node", harness], capture_output=True, text=True)
        if result.returncode != 0:
            raise AssertionError("the studio draft harness failed: %s"
                                 % ((result.stdout or "") + (result.stderr or ""))[:1200])
        print("studio draft harness OK")

    # The active chat is shared by the Studio, the sheet and the voices.
    harness = os.path.join(ROOT, "tests", "studio_session.mjs")
    if os.path.exists(harness):
        result = subprocess.run(["node", harness], capture_output=True, text=True)
        if result.returncode != 0:
            raise AssertionError("the studio session harness failed: %s"
                                 % ((result.stdout or "") + (result.stderr or ""))[:1200])
        print("studio session harness OK")

    # The grouped nav is data: a dropped child or a duplicate hash is a dead page.
    harness = os.path.join(ROOT, "tests", "nav_groups.mjs")
    if os.path.exists(harness):
        result = subprocess.run(["node", harness], capture_output=True, text=True)
        if result.returncode != 0:
            raise AssertionError("the nav groups harness failed: %s"
                                 % ((result.stdout or "") + (result.stderr or ""))[:1200])
        print("nav groups harness OK")

    # A provisioning run that is queued and one that failed look identical on a domain
    # row unless the row is told; this checks the real label function.
    harness = os.path.join(ROOT, "tests", "domains_state.mjs")
    if os.path.exists(harness):
        result = subprocess.run(["node", harness], capture_output=True, text=True)
        if result.returncode != 0:
            raise AssertionError("the domains state harness failed: %s"
                                 % ((result.stdout or "") + (result.stderr or ""))[:1200])
        print("domains state harness OK")

    # The server can protect a publish; the console must also show the target, the
    # draft-changes state and the undo, or the person is still guessing.
    harness = os.path.join(ROOT, "tests", "publish_state.mjs")
    if os.path.exists(harness):
        result = subprocess.run(["node", harness], capture_output=True, text=True)
        if result.returncode != 0:
            raise AssertionError("the publish state harness failed: %s"
                                 % ((result.stdout or "") + (result.stderr or ""))[:1200])
        print("publish state harness OK")

    # The build card is the non-technical door. It must offer the four kinds, render
    # choices and a review, and never expose the raw draft form.
    harness = os.path.join(ROOT, "tests", "build_card.mjs")
    if os.path.exists(harness):
        result = subprocess.run(["node", harness], capture_output=True, text=True)
        if result.returncode != 0:
            raise AssertionError("the build card harness failed: %s"
                                 % ((result.stdout or "") + (result.stderr or ""))[:1200])
        print("build card harness OK")


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
        print("JS checks skipped (node not installed)")
    check_suites()
    print("\nPREFLIGHT OK - safe to push")


if __name__ == "__main__":
    main()
