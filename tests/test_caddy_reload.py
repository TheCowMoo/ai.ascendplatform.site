"""Reloading Caddy without a shell command.

Run: .\\venv\\Scripts\\python.exe tests\\test_caddy_reload.py

The compose image is Python, not a Caddy install, so the reload is an HTTP POST of the
project's Caddyfile to Caddy's admin API. Implemented as a shell string it was refused by
the agent command policy (`curl` is not on the allowlist), so every live provisioning run
wrote the site block and then failed here - leaving a domain whose DNS was perfect with no
certificate. These checks pin the new shape: one POST to a URL the operator configured, a
body that is the mounted Caddyfile, and no route through the command allowlist.
"""

import io
import os
import sys
import tempfile
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="caddy-reload-"), "test.db").replace("\\", "/")
os.environ.pop("ALLOW_LIVE_ACTIONS", None)

import actions  # noqa: E402
import db  # noqa: E402
import provisioner  # noqa: E402

db.init_db()

FAILED = []
ADMIN = "http://caddy.test:2019"
BODY = "{}\n".encode("utf-8")


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


class FakeTarget:
    name = "local"

    def __init__(self, remote=False):
        self.is_remote = remote
        self.calls = []

    def run(self, command, job_id=None, emit=None, allow_mutating=True, summary=None,
            preapproved=False):
        self.calls.append({"command": command, "preapproved": preapproved})
        return {"ok": True, "dry_run": False, "output": "ran " + command}


class FakeResponse:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakePoster:
    """Stand in for urllib.request.urlopen, recording what would be sent."""

    def __init__(self, exc=None, status=200):
        self.requests = []
        self.exc = exc
        self.status = status

    def __call__(self, request, timeout=None):
        self.requests.append({
            "url": request.full_url,
            "method": request.get_method(),
            "data": request.data,
            "content_type": request.get_header("Content-type"),
        })
        if self.exc:
            raise self.exc
        return FakeResponse(self.status)


class live_actions:
    def __enter__(self):
        self.original = actions.is_dry_run
        actions.is_dry_run = lambda: False

    def __exit__(self, *exc):
        actions.is_dry_run = self.original


class gate_stub:
    def __init__(self, allowed=True):
        self.calls = []
        self.result = {"allowed": allowed, "dry_run": False,
                       "reason": "approved" if allowed else "denied by operator",
                       "approval_id": 1}

    def __enter__(self):
        self.original = actions.gate
        actions.gate = lambda tool, summary, args=None, job_id=None, emit=None: (
            self.calls.append({"tool": tool, "summary": summary}) or self.result)
        return self

    def __exit__(self, *exc):
        actions.gate = self.original


def _with_poster(poster, action):
    original = provisioner.urllib.request.urlopen
    provisioner.urllib.request.urlopen = poster
    try:
        return action()
    finally:
        provisioner.urllib.request.urlopen = original


def test_an_approved_run_posts_the_caddyfile():
    target = FakeTarget()
    poster = FakePoster()
    with live_actions(), gate_stub() as gate:
        result = _with_poster(poster, lambda: provisioner.reload_web_server(
            target, preapproved=True))
    check("one request is sent", len(poster.requests), 1)
    check("to the admin API's /load", poster.requests[0]["url"], ADMIN + "/load")
    check("as a POST", poster.requests[0]["method"], "POST")
    check("with the mounted Caddyfile as the body", poster.requests[0]["data"], BODY)
    check("typed as a Caddyfile", poster.requests[0]["content_type"], "text/caddyfile")
    check("a covered run asks nothing", gate.calls, [])
    check("it never becomes a shell command", target.calls, [])
    check("and the step succeeds", (result["ok"], result["dry_run"]), (True, False))
    check("saying which URL answered",
          "reloaded Caddy via " + ADMIN in result["output"], True)


def test_an_uncovered_run_asks_once():
    target = FakeTarget()
    poster = FakePoster()
    with live_actions(), gate_stub(allowed=True) as gate:
        result = _with_poster(poster, lambda: provisioner.reload_web_server(target))
    check("a reload on its own asks once", len(gate.calls), 1)
    check("naming the destination", ADMIN in gate.calls[0]["summary"], True)
    check("and then posts", (len(poster.requests), result["ok"]), (1, True))


def test_a_denied_reload_posts_nothing():
    target = FakeTarget()
    poster = FakePoster()
    with live_actions(), gate_stub(allowed=False):
        result = _with_poster(poster, lambda: provisioner.reload_web_server(target))
    check("denial stops before the request", poster.requests, [])
    check("and reports the reason", result["ok"], False)


def test_dry_run_posts_nothing():
    target = FakeTarget()
    poster = FakePoster()
    result = _with_poster(poster, lambda: provisioner.reload_web_server(
        target, preapproved=True))
    check("dry-run sends no request", poster.requests, [])
    check("and simulates the step", (result["ok"], result["dry_run"]), (True, True))
    check("with the note in the output", "would POST" in result["output"], True)


def test_caddys_rejection_is_surfaced():
    error = urllib.error.HTTPError(
        ADMIN + "/load", 400, "Bad Request", {},
        io.BytesIO(b"caddyfile: line 3: unrecognized directive"))
    target = FakeTarget()
    poster = FakePoster(exc=error)
    with live_actions():
        result = _with_poster(poster, lambda: provisioner.reload_web_server(
            target, preapproved=True))
    check("a refused config is a failure", result["ok"], False)
    check("and Caddy's own words are kept",
          "unrecognized directive" in result["output"] and "400" in result["output"], True)


def test_a_remote_target_uses_its_own_command():
    target = FakeTarget(remote=True)
    poster = FakePoster()
    with live_actions():
        result = _with_poster(poster, lambda: provisioner.reload_web_server(
            target, preapproved=True))
    check("a remote server is reloaded over its own path", len(target.calls), 1)
    check("with the configured command", target.calls[0]["command"],
          provisioner.CADDY_RELOAD_CMD)
    check("and the approval carried through", target.calls[0]["preapproved"], True)
    check("nothing is posted to this app's Caddy", poster.requests, [])
    check("the step succeeds", result["ok"], True)


def test_a_bad_configuration_says_so():
    target = FakeTarget()
    original = (provisioner.CADDY_ADMIN_URL, provisioner.CADDYFILE)
    provisioner.CADDY_ADMIN_URL = "file:///etc/passwd"
    try:
        with live_actions():
            result = provisioner.reload_web_server(target, preapproved=True)
    finally:
        provisioner.CADDY_ADMIN_URL = original[0]
    check("a non-http admin URL is refused", result["ok"], False)
    check("with the URL named", "http(s)" in result["output"], True)

    provisioner.CADDYFILE = os.path.join(tempfile.gettempdir(), "no-such-caddyfile-auto")
    try:
        with live_actions():
            result = provisioner.reload_web_server(target, preapproved=True)
    finally:
        provisioner.CADDYFILE = original[1]
    check("a missing Caddyfile is refused", result["ok"], False)
    check("with the path named", "cannot read" in result["output"], True)


if __name__ == "__main__":
    original_admin = provisioner.CADDY_ADMIN_URL
    provisioner.CADDY_ADMIN_URL = ADMIN
    handle, provisioner.CADDYFILE = tempfile.mkstemp(prefix="caddyfile-", suffix=".Caddyfile")
    with os.fdopen(handle, "wb") as caddyfile:
        caddyfile.write(BODY)
    try:
        test_an_approved_run_posts_the_caddyfile()
        test_an_uncovered_run_asks_once()
        test_a_denied_reload_posts_nothing()
        test_dry_run_posts_nothing()
        test_caddys_rejection_is_surfaced()
        test_a_remote_target_uses_its_own_command()
        test_a_bad_configuration_says_so()
    finally:
        provisioner.CADDY_ADMIN_URL = original_admin
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nCADDY RELOAD OK - one POST of the Caddyfile, no shell command to refuse")
