"""Automatic provisioning: DNS going green is what starts the run, exactly once.

Run: .\\venv\\Scripts\\python.exe tests\\test_auto_provision.py

The bug this pins: the hourly sweep promised "point it here and SSL issues itself" but
only ever *probed*. Nothing wrote a Caddy site block, so a correctly pointed domain kept
answering the browser with ERR_SSL_PROTOCOL_ERROR forever. The checks below cover the
three ways to get it wrong again - a www row invented for a subdomain, a sweep that never
queues, and a sweep that queues every hour - plus the route the Check button calls.
"""

import os
import ssl
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# A throwaway database: this suite creates sites and jobs, and it must not add rows to
# the operator's `data/app.db` (or read a live-actions flag from it).
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="auto-prov-"), "test.db").replace("\\", "/")
os.environ.pop("ALLOW_LIVE_ACTIONS", None)
os.environ.pop("JOB_BACKEND", None)
os.environ.pop("REDIS_URL", None)
os.environ["SERVER_IP"] = "203.0.113.7"
os.environ["SITE_VERIFY_MINUTES"] = "0"

import app as appmod  # noqa: E402
import actions  # noqa: E402
import db  # noqa: E402
import jobs  # noqa: E402
import provisioner  # noqa: E402
import scheduler  # noqa: E402
import sites as sites_service  # noqa: E402

FAILED = []
IP = "203.0.113.7"


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


class live_mode:
    """Pretend ALLOW_LIVE_ACTIONS=1 for the block, without touching the database."""

    def __enter__(self):
        self.original = actions.is_dry_run
        actions.is_dry_run = lambda: False
        return self

    def __exit__(self, *exc):
        actions.is_dry_run = self.original


class recorded_submit:
    """Capture job ids instead of running them - a test must not provision anything."""

    def __enter__(self):
        self.submitted = []
        self.original = jobs.submit
        jobs.submit = lambda job_id: self.submitted.append(job_id) or "local"
        return self

    def __exit__(self, *exc):
        jobs.submit = self.original


def finish(job_id, status="done"):
    with db.get_connection() as conn:
        conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))


def test_apex_and_the_www_rows():
    check("example.com is an apex name", provisioner.is_apex("example.com"), True)
    check("test2.example.com is a host under one",
          provisioner.is_apex("test2.example.com"), False)
    check("a two-level suffix is still an apex",
          provisioner.is_apex("example.co.uk"), True)
    check("but a host under it is not", provisioner.is_apex("www.example.co.uk"), False)
    check("one label is not a domain", provisioner.is_apex("localhost"), False)

    rows = provisioner.dns_records("test2.example.com", IP)
    check("a subdomain gets one record", [(r["type"], r["name"]) for r in rows], [("A", "@")])
    rows = provisioner.dns_records("example.co.uk", IP)
    check("an apex still gets the www CNAME",
          [(r["type"], r["name"]) for r in rows], [("A", "@"), ("CNAME", "www")])

    # The report is what the panel renders. A www row on a subdomain used to show
    # "missing" and hold `ok` false, so a correct record read as a broken domain.
    real_types, real_cname = provisioner.resolve_types, provisioner.resolve_cname
    try:
        provisioner.resolve_types = lambda name, timeout=5: (
            [IP] if name in ("test2.example.com", "example.com", "www.example.com") else [], [])
        provisioner.resolve_cname = lambda name, timeout=5: (
            "example.com" if name == "www.example.com" else "")
        report = provisioner.dns_report("test2.example.com", IP)
        check("a subdomain report has no www row", [r["name"] for r in report["rows"]],
              ["@", "@"])
        check("and a correct subdomain is ok", report["ok"], True)
        report = provisioner.dns_report("example.com", IP)
        check("an apex report keeps both www rows", [r["name"] for r in report["rows"]],
              ["@", "www", "@", "www"])
        check("and is ok when www aliases it", report["ok"], True)
    finally:
        provisioner.resolve_types, provisioner.resolve_cname = real_types, real_cname

    block = provisioner.render_vhost("example.com", "/srv/sites/example.com/public_html",
                                     ["example.com", "www.example.com", "www.example.com"])
    check("a host listed twice is written once",
          block.count("www.example.com"), 1)


def test_the_handshake_hint():
    reason, hint = provisioner.describe_probe_error(ssl.SSLError(1, "WRONG_VERSION_NUMBER"))
    check("a refused TLS handshake names the missing site block",
          "not configured" in hint, True)
    check("and names the error the browser shows", "ERR_SSL_PROTOCOL_ERROR" in hint, True)
    reason, hint = provisioner.describe_probe_error(ConnectionResetError("reset by peer"))
    check("a reset is the same failed handshake", "TLS handshake" in hint, True)
    # A certificate that exists but is wrong must not be described as missing.
    reason, hint = provisioner.describe_probe_error(
        ssl.SSLCertVerificationError("certificate verify failed"))
    check("a bad certificate keeps its own advice", "could not be trusted" in hint, True)


def test_the_queue():
    site = sites_service.create_site({"domain": "auto.example.com"})
    with recorded_submit() as recorder, live_mode():
        job = sites_service.queue_provision(site)
        check("a green domain queues one run", bool(job), True)
        check("under the provisioning task", (job or {}).get("task"), "site_provision")
        check("with the domain as its prompt", (job or {}).get("prompt"), "auto.example.com")
        check("and it reaches the job engine", recorder.submitted, [job["id"]])
        check("a second call does not double up", sites_service.queue_provision(site), None)
        check("the open job is the one the UI shows",
              (sites_service.open_provision_job(site) or {}).get("id"), job["id"])

        finish(job["id"], "done")
        again = sites_service.queue_provision(site)
        check("after the run finishes it may run again", bool(again), True)
        finish(again["id"], "done")

        third = sites_service.queue_provision(site)
        finish(third["id"], "failed")
        check("three attempts in a day is the cap",
              sites_service.queue_provision(site), None)
        check("and the row says why",
              "paused after 3 attempts" in (sites_service.get_site(site["id"]) or {}).get(
                  "last_error", ""), True)
        manual = sites_service.queue_provision(site, manual=True)
        check("pressing Provision ignores the cap", bool(manual), True)
        finish(manual["id"], "done")

    with recorded_submit() as recorder:
        fresh = sites_service.create_site({"domain": "dry.example.com"})
        check("dry-run queues nothing", sites_service.queue_provision(fresh), None)
        check("and submits nothing", recorder.submitted, [])


def test_the_routes():
    client = appmod.app.test_client()
    login = client.post("/api/auth/login", json={"email": "admin@example.com",
                                                "password": "admin123"})
    check("the test operator can sign in", login.status_code, 200)

    site = sites_service.create_site({"domain": "route.example.com"})
    original = provisioner.check_dns
    provisioner.check_dns = lambda domain, server_ip=None: {
        "domain": domain, "ok": True, "reason": "", "resolved": [IP], "expected": IP}
    try:
        with recorded_submit() as recorder, live_mode():
            data = client.post("/api/sites/%d/check" % site["id"]).get_json() or {}
            check("Check DNS queues the run it just made possible",
                  (data.get("provision") or {}).get("task"), "site_provision")
            check("and reports that it is needed", data.get("needs_provision"), True)
            check("submitted exactly once", len(recorder.submitted), 1)

            first = client.post("/api/sites/%d/provision" % site["id"]).get_json() or {}
            second = client.post("/api/sites/%d/provision" % site["id"]).get_json() or {}
            check("a second Provision returns the run already in flight",
                  (second.get("job") or {}).get("id"), (first.get("job") or {}).get("id"))
            check("and says so", second.get("already"), True)
    finally:
        provisioner.check_dns = original

    check("Check DNS needs a login",
          appmod.app.test_client().post("/api/sites/%d/check" % site["id"]).status_code, 401)


def test_the_sweep():
    pending = sites_service.create_site({"domain": "sweep.example.com"})
    live = sites_service.create_site({"domain": "already.example.com"})
    sites_service.update_site(live["id"], {"status": "live"})

    queued = []
    originals = (provisioner.dns_report, provisioner.live_probe,
                 sites_service.queue_provision)
    provisioner.dns_report = lambda domain, server_ip_value=None: {
        "ok": True, "resolved": [IP], "expected": IP, "reason": "", "rows": []}
    provisioner.live_probe = lambda domain, **kwargs: {
        "ok": False, "error": "ssl", "hint": "no site block", "status_code": None}
    sites_service.queue_provision = (
        lambda site, requester_id=None, manual=False: queued.append(site["domain"]))
    try:
        scheduler.verify_pending()
    finally:
        (provisioner.dns_report, provisioner.live_probe,
         sites_service.queue_provision) = originals

    check("the sweep queues a domain DNS now points here",
          "sweep.example.com" in queued, True)
    check("it does not touch a live site", "already.example.com" in queued, False)
    check("once per domain per sweep", queued.count("sweep.example.com"), 1)
    check("and the row is marked dns_ok",
          (sites_service.get_site(pending["id"]) or {}).get("status"), "dns_ok")


def test_the_tab_says_what_will_happen():
    """Static, like the other UI guards: the endpoint existing is not the same as use."""
    script = open(os.path.join(ROOT, "admin", "admin.js"), encoding="utf-8").read()
    check("the Domains tab explains dry-run",
          "Dry-run: DNS checks and certificates" in script, True)
    check("it reads the job list for provisioning state", "'/api/jobs'" in script, True)
    check("a queued run is visible on the row", "provisionState(prov)" in script, True)
    check("Check DNS reports the queued job", "provisioning queued (job #" in script, True)
    check("the panel explains the SSL error before provisioning",
          "ERR_SSL_PROTOCOL_ERROR" in script, True)


if __name__ == "__main__":
    test_apex_and_the_www_rows()
    test_the_handshake_hint()
    test_the_queue()
    test_the_routes()
    test_the_sweep()
    test_the_tab_says_what_will_happen()
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nAUTO PROVISION OK - DNS going green starts one run, once, with one approval")

