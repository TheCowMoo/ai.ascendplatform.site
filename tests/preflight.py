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
    missing = [frag for frag in ("/etc/caddy/sites", "/srv/sites", "/srv/caddy/Caddyfile")
               if frag not in worker]
    if missing:
        raise AssertionError(
            "the worker service is missing %s - provisioning would write where Caddy "
            "cannot see it" % ", ".join(missing))
    # A single-file mount is pinned to the inode git replaces, so a pull left Caddy
    # reading the previous Caddyfile until the container was recreated. Mount the dir.
    for name in ("app", "worker", "caddy"):
        block = text.split(name + ":", 1)[-1]
        if "./caddy/Caddyfile:" in block:
            raise AssertionError(
                "%s mounts caddy/Caddyfile as a file - mount ./caddy as a directory "
                "instead, or edits will not take effect until the container is recreated"
                % name)
    print("worker shares the site paths with caddy")


def check_caddyfile():
    """Formatting nits Caddy warns about on every reload, caught before they ship."""
    path = os.path.join(ROOT, "caddy", "Caddyfile")
    if not os.path.exists(path):
        return
    with open(path, "rb") as handle:
        raw = handle.read()
    assert b"\r" not in raw, "caddy/Caddyfile has CRLF line endings - `caddy fmt` will warn"
    assert raw.endswith(b"\n"), "caddy/Caddyfile must end with a newline"
    assert b"header_up X-Forwarded-Proto" not in raw, "Caddy sets X-Forwarded-Proto itself"
    print("caddy/Caddyfile is clean")


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
    try:
        check_js()
    except FileNotFoundError:
        print("admin.js skipped (node not installed)")
    check_suites()
    print("\nPREFLIGHT OK - safe to push")


if __name__ == "__main__":
    main()
