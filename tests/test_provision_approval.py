"""One provisioning run asks one question.

Run: .\\venv\\Scripts\\python.exe tests\\test_provision_approval.py

Provisioning used to gate index.html, the site block and the Caddy reload separately. In
live mode that is three cards to approve, five minutes each - and missing the last one
left a site fully configured with no certificate, which is the failure that looks like
"SSL is broken". These checks pin the new contract: one gate per run, a denial stops
before any write, and an approval does not become a licence to skip policy.
"""

import asyncio
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="prov-approval-"), "test.db").replace("\\", "/")
os.environ.pop("ALLOW_LIVE_ACTIONS", None)
os.environ.pop("JOB_BACKEND", None)
os.environ.pop("REDIS_URL", None)

import actions  # noqa: E402
import db  # noqa: E402
import provisioner  # noqa: E402
import sites as sites_service  # noqa: E402
import tools.site as site_tools  # noqa: E402

db.init_db()

FAILED = []


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


LIVE = {"ok": True, "status": "live", "url": "https://approve.example.com",
        "steps": [], "dns": {"resolved": [], "expected": "", "ok": True}}


def run_task(domain, decision):
    """Run the real job task with the gate and the provisioner stubbed."""
    calls = {"gate": [], "provision": []}
    original_gate, original_provision = actions.gate, provisioner.provision
    actions.gate = lambda tool, summary, args=None, job_id=None, emit=None: (
        calls["gate"].append({"tool": tool, "summary": summary, "args": args})
        or decision)
    provisioner.provision = lambda site, **kwargs: (
        calls["provision"].append(kwargs) or dict(LIVE))

    async def go():
        job = {"id": 41, "prompt": domain, "requester_id": None}
        return await site_tools.site_provision_task(job, lambda *a: None)

    try:
        result = asyncio.run(go())
    finally:
        actions.gate, provisioner.provision = original_gate, original_provision
    return result, calls


def test_an_approved_run_asks_once():
    result, calls = run_task("approve.example.com", {"allowed": True, "dry_run": False,
                                                     "reason": "approved", "approval_id": 9})
    check("exactly one approval for the whole run", len(calls["gate"]), 1)
    check("it names the domain", "approve.example.com" in calls["gate"][0]["summary"], True)
    check("and says what it covers", "site block" in calls["gate"][0]["summary"], True)
    check("the run reached the provisioner", len(calls["provision"]), 1)
    check("with the approval already in hand",
          calls["provision"][0].get("preapproved"), True)
    check("the site is recorded live",
          (sites_service.get_site_by_domain("approve.example.com") or {}).get("status"),
          "live")
    check("and the transcript has the outcome", "ok=True status=live" in result, True)


def test_a_denied_run_writes_nothing():
    result, calls = run_task("deny.example.com", {"allowed": False, "dry_run": False,
                                                   "reason": "denied by operator",
                                                   "approval_id": 10})
    check("the provisioner is never called", calls["provision"], [])
    check("the job says why", "denied by operator" in result, True)
    site = sites_service.get_site_by_domain("deny.example.com") or {}
    check("the site is marked an error", site.get("status"), "error")
    check("with the reason on the row", "not approved" in (site.get("last_error") or ""), True)


def test_preapproval_is_not_a_policy_bypass():
    gate_calls = []
    original_gate, original_dry = actions.gate, actions.is_dry_run
    actions.gate = lambda *a, **kw: gate_calls.append(a) or {
        "allowed": True, "dry_run": False, "reason": "approved", "approval_id": 1}
    actions.is_dry_run = lambda: False          # the live mode this option exists for
    try:
        allowed = provisioner._authorise("file_write", "write index.html", {}, None, None,
                                         preapproved=True)
    finally:
        actions.gate, actions.is_dry_run = original_gate, original_dry
    check("a covered write asks nothing", (allowed["allowed"], gate_calls),
          (True, []))
    check("and is not marked as a dry-run", allowed["dry_run"], False)

    blocked = provisioner._authorise("shell", "wipe", {"command": "rm -rf /"}, None, None,
                                     command="rm -rf /", preapproved=True)
    check("policy still blocks an approved run", (blocked["allowed"], blocked.get("blocked")),
          (False, True))

    # With no live actions, a preapproved step must still be simulated, not executed.
    simulated = provisioner._authorise("shell", "true", {"command": "true"}, None, None,
                                       command="true", preapproved=True)
    check("dry-run still simulates a covered step",
          (simulated["allowed"], simulated["dry_run"], simulated["reason"]),
          (True, True, "dry-run"))


if __name__ == "__main__":
    test_an_approved_run_asks_once()
    test_a_denied_run_writes_nothing()
    test_preapproval_is_not_a_policy_bypass()
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nPROVISION APPROVAL OK - one run, one question, policy still applies")