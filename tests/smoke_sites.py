"""Sites, servers, approvals and the hosting tools (stage S5).

Run: .\\venv\\Scripts\\python.exe tests\\smoke_sites.py

Everything runs in dry-run, so nothing is provisioned, executed or sent.
"""

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.pop("ALLOW_LIVE_ACTIONS", None)
os.environ["SERVER_IP"] = "203.0.113.10"

import app as appmod  # noqa: E402
import sites as sites_service  # noqa: E402
import tools.site as site_tools  # noqa: E402


def login():
    client = appmod.app.test_client()
    resp = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "admin123"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return client


def reset_test_sites():
    """Make the test re-runnable: domains are unique, so clear leftovers first."""
    for domain in ("demo.test", "agent-demo.test"):
        existing = sites_service.get_site_by_domain(domain)
        if existing:
            sites_service.delete_site(existing["id"])
            print("cleaned up leftover site", domain)


def test_ssh_hosts_crud(client):
    resp = client.get("/api/ssh-hosts")
    assert resp.status_code == 200
    assert resp.get_json()["dry_run"] is True, "S5 tests must run in dry-run"
    print("existing hosts:", len(resp.get_json()["hosts"]))

    resp = client.post("/api/ssh-hosts", json={
        "label": "web1", "host": "web1.example.com", "username": "deploy",
        "allowed_root": "/home/deploy/sites",
    })
    assert resp.status_code == 201, resp.get_data(as_text=True)
    host = resp.get_json()["host"]
    print("created host id=%s label=%s port=%s has_key=%s"
          % (host["id"], host["label"], host["port"], host["has_key"]))
    assert host["port"] == 22
    assert host["sites_root"] == "/home/deploy/sites"

    resp = client.put("/api/ssh-hosts/%d" % host["id"], json={"enabled": False})
    assert resp.get_json()["host"]["enabled"] is False

    body = client.post("/api/ssh-hosts/%d/test" % host["id"]).get_json()
    print("probe (dry-run):", body["output"])
    assert body["dry_run"] is True and "ALLOW_LIVE_ACTIONS" in body["output"]

    assert client.delete("/api/ssh-hosts/%d" % host["id"]).status_code == 200
    print("SSH HOSTS OK")


def test_sites_crud(client):
    resp = client.post("/api/sites", json={"domain": "demo.test", "target": "local"})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    site = resp.get_json()["site"]
    print("created site id=%s domain=%s status=%s" % (site["id"], site["domain"], site["status"]))

    assert client.post("/api/sites", json={"domain": "demo.test"}).status_code == 409
    assert client.post("/api/sites", json={"domain": "../etc/passwd"}).status_code == 400
    assert client.post("/api/sites", json={"domain": "not a domain"}).status_code == 400
    print("duplicate and invalid domains refused")

    listing = client.get("/api/sites").get_json()
    print("targets:", [t["label"] for t in listing["targets"]])
    assert listing["dry_run"] is True
    assert listing["server_ip"] == "203.0.113.10"

    dns = client.get("/api/sites/%d/dns" % site["id"]).get_json()
    print("dns records:", json.dumps(dns["records"]))
    assert dns["records"][0]["value"] == "203.0.113.10"
    assert dns["records"][1]["name"] == "www.demo.test"

    check = client.post("/api/sites/%d/check" % site["id"]).get_json()["check"]
    print("dns check: ok=%s reason=%s" % (check["ok"], check["reason"]))
    assert check["ok"] is False
    print("SITES OK")
    return site


def test_provision_job(client, site):
    resp = client.post("/api/sites/%d/provision" % site["id"])
    assert resp.status_code == 201, resp.get_data(as_text=True)
    job_id = resp.get_json()["job"]["id"]
    print("queued provision job", job_id)

    data = None
    for _ in range(60):
        data = client.get("/api/jobs/%d/events?after=0" % job_id).get_json()
        if data["job"]["status"] in ("done", "failed", "cancelled"):
            break
        time.sleep(0.2)

    print("job status:", data["job"]["status"])
    for event in data["events"]:
        print("  [%s] %s: %s" % (event["type"], event["source"], (event["content"] or "")[:95]))

    assert data["job"]["status"] == "done", data["job"]
    assert "https://demo.test" in (data["job"]["result"] or ""), data["job"]["result"]

    updated = sites_service.get_site_by_domain("demo.test")
    print("site now: status=%s url=%s" % (updated["status"], updated["url"]))
    assert updated["url"] == "https://demo.test"
    assert updated["status"] == "pending_dns", updated["status"]
    assert updated["dns_last_check"], "the DNS check should be recorded"
    print("PROVISION JOB OK")


def test_approvals_endpoints(client):
    resp = client.get("/api/approvals")
    assert resp.status_code == 200
    body = resp.get_json()
    print("approvals:", len(body["approvals"]), "| dry_run:", body["dry_run"])
    assert body["dry_run"] is True, "dry-run should create no approval requests"

    assert client.post("/api/approvals/999999/approve").status_code == 404
    assert client.post("/api/approvals/1/bogus").status_code == 400
    print("APPROVALS OK")


def test_agent_tools_dry_run():
    """The agent-facing hosting tools, driven directly, in dry-run."""
    events = []
    emit = lambda source, kind, text: events.append((source, kind, text))

    provision = site_tools._build_provision_site(emit, None)
    result = asyncio.run(provision("agent-demo.test"))
    print(result)
    assert "https://agent-demo.test" in result, result
    assert any("dry-run" in text for _, _, text in events), events

    listing = asyncio.run(site_tools._build_list_sites(emit, None)())
    assert "agent-demo.test" in listing, listing
    print("list_sites includes the new site")

    dns = asyncio.run(site_tools._build_check_site_dns(emit, None)("agent-demo.test"))
    assert "records_to_create" in dns, dns
    print("check_site_dns OK")

    missing = asyncio.run(site_tools._build_write_site_content(emit, None)("nope.test", "<p>x</p>"))
    assert "No site record" in missing, missing
    print("AGENT TOOLS (dry-run) OK")


if __name__ == "__main__":
    reset_test_sites()
    flask_client = login()
    test_ssh_hosts_crud(flask_client)
    created_site = test_sites_crud(flask_client)
    test_provision_job(flask_client, created_site)
    test_approvals_endpoints(flask_client)
    test_agent_tools_dry_run()
    print("SITES + SERVERS OK")

