"""List responses can omit full documents without breaking the editor.

Run: .\\venv\\Scripts\\python.exe tests\\test_list_summary.py

The dashboard and the pickers ask for ``summary=1``. The draft/page editors fetch
the one row they open from its own endpoint, so a summary list must not carry every
document and a full request must still return it.
"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="list-summary-"), "test.db").replace("\\", "/")
os.environ["SCHEDULER_ENABLED"] = "0"
os.environ.pop("ALLOW_LIVE_ACTIONS", None)

import app as appmod  # noqa: E402
import db  # noqa: E402
import deliverables as deliverables_service  # noqa: E402
import orgs as orgs_service  # noqa: E402
import sites as sites_service  # noqa: E402

db.init_db()

FAILED = []


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


def main():
    client = appmod.app.test_client()
    login = client.post("/api/auth/login",
                        json={"email": "admin@example.com", "password": "admin123"})
    assert login.status_code == 200, login.get_data(as_text=True)
    org = orgs_service.create_org({"name": "Summary Client"})["id"]
    client.post("/api/session/org", json={"org_id": org})

    deliverables_service.create_deliverable(
        {"kind": "website", "title": "Summary page", "content": "<p>full body</p>"},
        org_id=org)
    sites_service.create_site(
        {"domain": "summary.test", "content_html": "This site is ready."}, org_id=org)

    summary = client.get("/api/deliverables?summary=1").get_json()["deliverables"][0]
    check("a summary deliverable omits the document", "content" in summary, False)
    full = client.get("/api/deliverables").get_json()["deliverables"][0]
    check("a normal deliverable still has it", full["content"], "<p>full body</p>")

    summary_site = client.get("/api/sites?summary=1").get_json()["sites"][0]
    check("a summary site omits the page", "content_html" in summary_site, False)
    check("but says it is the starter", summary_site["starter"], True)
    full_site = client.get("/api/sites").get_json()["sites"][0]
    check("a normal site still has the page", "content_html" in full_site, True)

    service_summary = deliverables_service.list_deliverables(
        org_id=org, include_content=False)[0]
    check("the service option is honoured", "content" in service_summary, False)
    service_site = sites_service.list_sites(org_id=org, include_content=False)[0]
    check("the site service option is honoured", "content_html" in service_site, False)

    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nLIST SUMMARY OK - lists stay small, editors fetch the full row")


if __name__ == "__main__":
    main()
