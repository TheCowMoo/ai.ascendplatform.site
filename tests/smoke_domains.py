"""DNS truth: per-record statuses, so "it doesn't work" becomes a named record.

Run: .\\venv\\Scripts\\python.exe tests\\smoke_domains.py

No network: the resolver and Cloudflare's ranges are injected. What is being tested is the
*judgement* - that a missing www is named, that a proxied domain says so instead of
"points elsewhere", that an AAAA pointing at somebody else is called out (browsers try it
first), and that a server with no known IP explains itself rather than reporting failure.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.pop("ALLOW_LIVE_ACTIONS", None)

import provisioner  # noqa: E402

HERE = "203.0.113.10"
ELSEWHERE = "198.51.100.7"
CF = "104.16.1.5"          # inside Cloudflare's published ranges
DOMAIN = "example.test"


def inject(apex=(), www=(), ip=HERE, cloudflare=(CF,)):
    """Point the resolver and the Cloudflare list at fixed answers."""
    def fake_resolve(name, timeout=5):
        v4 = list(apex) if name == DOMAIN else (list(www) if name.startswith("www.") else [])
        v6 = [a for a in v4 if ":" in a]
        return [a for a in v4 if ":" not in a], v6
    provisioner.resolve_types = fake_resolve
    provisioner.server_ip_source = lambda: (ip, "setting" if ip else "")
    provisioner._in_cloudflare = lambda address: address in cloudflare


def row(report, rtype, name):
    for item in report["rows"]:
        if item["type"] == rtype and item["name"] == name:
            return item
    raise AssertionError("no %s %s row in %s" % (rtype, name, report["rows"]))


def test_pointed_correctly_is_green():
    inject(apex=[HERE], www=[HERE])
    report = provisioner.dns_report(DOMAIN)
    assert report["ok"] is True, report
    assert report["reason"] == "", report
    assert row(report, "A", "@")["status"] == "ok"
    assert provisioner.check_dns(DOMAIN)["ok"] is True
    print("apex + www pointing here -> green")


def test_a_missing_www_is_named_not_swallowed():
    """www is optional, but it must be visible: silence is how people lose an hour."""
    inject(apex=[HERE], www=[])
    report = provisioner.dns_report(DOMAIN)
    www = row(report, "A", "www")
    assert www["status"] == "missing", www
    assert "optional" in www["note"], www
    assert report["ok"] is True, "a missing www must not block go-live"
    print("missing www ->", www["note"])


def test_pointing_somewhere_else_says_where():
    inject(apex=[ELSEWHERE], www=[ELSEWHERE])
    report = provisioner.dns_report(DOMAIN)
    assert report["ok"] is False
    assert report["reason"] == "points elsewhere", report
    apex = row(report, "A", "@")
    assert apex["status"] == "wrong"
    assert ELSEWHERE in apex["note"] and HERE in apex["note"], apex["note"]
    print("wrong A ->", apex["note"])


def test_ipv6_pointing_elsewhere_is_called_out():
    """The A record can be right while the browser goes to an AAAA nobody remembered."""
    inject(apex=[HERE, "2a00:1450::1"], www=[HERE])
    report = provisioner.dns_report(DOMAIN)
    aaaa = row(report, "AAAA", "@")
    assert aaaa["status"] == "wrong", aaaa
    assert "browsers" in aaaa["note"].lower(), aaaa["note"]
    assert report["ok"] is True, "the required A record is still right"
    print("stray AAAA ->", aaaa["note"])


def test_cloudflare_proxy_is_explained():
    inject(apex=[CF], www=[CF])
    report = provisioner.dns_report(DOMAIN)
    assert report["proxied"] is True, report
    assert report["ok"] is False
    assert report["reason"] == "proxied by Cloudflare"
    apex = row(report, "A", "@")
    assert "DNS-only" in apex["note"], apex["note"]
    print("proxied ->", apex["note"][:60], "...")


def test_no_known_ip_explains_itself():
    """The bug that made this unpassable: an empty SERVER_IP returned ok=False silently."""
    inject(apex=[HERE], www=[HERE], ip="")
    report = provisioner.dns_report(DOMAIN)
    assert report["ok"] is False
    assert report["reason"] == "no server IP configured", report
    apex = row(report, "A", "@")
    assert apex["status"] == "unknown"
    assert "no public IP" in apex["note"], apex["note"]
    print("no IP ->", apex["note"])


def test_the_records_use_the_resolved_address():
    inject(apex=[HERE], www=[HERE])
    records = provisioner.dns_records(DOMAIN)
    assert [r["name"] for r in records] == ["@", "www"], records
    assert all(r["value"] == HERE for r in records), records
    assert records[1]["host"] == "www." + DOMAIN
    print("records ->", ", ".join("%s %s" % (r["type"], r["name"]) for r in records))


_CERT = {
    "issuer": ((("organizationName", "Let's Encrypt"),),),
    "subjectAltName": (("DNS", DOMAIN), ("DNS", "www." + DOMAIN)),
    "notAfter": "Jan 24 12:00:00 2027 GMT",
}


def test_a_live_site_is_verified_with_its_certificate():
    probe = provisioner.live_probe(DOMAIN, fetcher=lambda: (200, _CERT))
    assert probe["ok"] is True, probe
    assert probe["issuer"] == "Let's Encrypt", probe
    assert probe["expires"] == "Jan 24 12:00:00 2027 GMT"
    assert DOMAIN in probe["names"], probe
    print("200 + certificate ->", probe["issuer"], "until", probe["expires"])


def test_a_proxy_answering_is_explained_not_retried_blindly():
    probe = provisioner.live_probe(DOMAIN, fetcher=lambda: (522, _CERT))
    assert probe["ok"] is False
    assert "cloudflare" in probe["hint"].lower(), probe["hint"]
    print("522 ->", probe["hint"][:60], "...")


def test_refused_and_untrusted_and_missing_dns_get_different_advice():
    refused = provisioner.live_probe(DOMAIN, fetcher=lambda: (_ for _ in ()).throw(
        ConnectionRefusedError("Connection refused")))
    assert "443" in refused["hint"], refused["hint"]

    untrusted = provisioner.live_probe(DOMAIN, fetcher=lambda: (_ for _ in ()).throw(
        __import__("ssl").SSLCertVerificationError(
            "certificate verify failed: Hostname mismatch, certificate is not valid for 'x'")))
    assert "different domain" in untrusted["hint"], untrusted["hint"]

    missing = provisioner.live_probe(DOMAIN, fetcher=lambda: (_ for _ in ()).throw(
        OSError("[Errno -2] Name or service not known")))
    assert "does not resolve" in missing["hint"], missing["hint"]
    print("refused / untrusted / unresolved each get their own advice")


def test_a_healthy_certificate_but_a_broken_page_is_not_live():
    probe = provisioner.live_probe(DOMAIN, fetcher=lambda: (502, _CERT))
    assert probe["ok"] is False
    assert probe["issuer"] == "Let's Encrypt", "the certificate still tells us something"
    assert "not healthy" in probe["hint"], probe["hint"]
    print("502 ->", probe["hint"])


def main():
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print("ok", test.__name__)
    print("\nDNS REPORT OK - every record is judged separately, including the awkward ones")


if __name__ == "__main__":
    main()
