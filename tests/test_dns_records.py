"""The DNS list: what to publish, and whether what is published is right.

The flow a person actually uses is "type my domain, copy these rows". Two things can go wrong
and both are tested here without touching the network: the list can recommend one record while
the verdict demands another, and the copy text can drift from the table it claims to copy.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.pop("ALLOW_LIVE_ACTIONS", None)

import app as appmod  # noqa: E402
import provisioner  # noqa: E402

FAILED = []


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


IP = "203.0.113.7"


def fake(cname, seen4):
    """Pretend DNS says this - the judgement is the code under test, not the resolver."""
    provisioner.resolve_cname = lambda name, timeout=5: cname
    provisioner.resolve_types = lambda name, timeout=5: ([seen4] if seen4 else [], [])


def test_www_verdicts():
    fake("example.com", IP)
    row = provisioner._www_row("example.com", IP)
    check("a CNAME to the domain is ok", row["status"], "ok")
    check("and is recognised as the better shape", "IP change" in row["note"], True)

    fake("elsewhere.test", IP)
    check("a CNAME to somewhere else is wrong",
          provisioner._www_row("example.com", IP)["status"], "wrong")

    fake("", IP)
    row = provisioner._www_row("example.com", IP)
    check("an A record is accepted, not failed", row["status"], "ok")
    check("but is reported as the A shape", row["type"], "A")
    check("with the advice in the note", "would survive an IP change" in row["note"], True)

    fake("", "")
    check("nothing published is missing",
          provisioner._www_row("example.com", IP)["status"], "missing")

    check("with no server IP there is no verdict",
          provisioner._www_row("example.com", "")["status"], "unknown")


def test_the_list_and_the_copy():
    rows = provisioner.dns_records("example.com", IP)
    check("two records", [(r["type"], r["name"]) for r in rows],
          [("A", "@"), ("CNAME", "www")])
    check("the apex points at the server", rows[0]["value"], IP)
    check("www points at the domain, not the IP", rows[1]["value"], "example.com")
    check("each row carries its own copy line", rows[1]["copy"],
          "CNAME  www  example.com  TTL 3600")
    text = provisioner.dns_records_text("example.com", IP)
    check("the block contains every row", all(row["copy"] in text for row in rows), True)
    check("and says where to put them", "nameservers" in text, True)


def test_preview_endpoint():
    client = appmod.app.test_client()
    check("a preview needs a login",
          client.post("/api/domains/preview", json={"domain": "example.com"}).status_code, 401)
    client.post("/api/auth/login", json={"email": "admin@example.com", "password": "admin123"})
    fake("example.com", IP)

    junk = client.post("/api/domains/preview", json={"domain": "not a domain"})
    check("nonsense is refused with a sentence", junk.status_code, 400)
    check("and the sentence says what to type",
          "try example.com" in (junk.get_json() or {}).get("error", ""), True)

    good = client.post("/api/domains/preview",
                       json={"domain": "https://Example.com/some/path"})
    data = good.get_json() or {}
    check("a pasted URL is reduced to its domain", data.get("domain"), "example.com")
    check("the records come back ready to copy",
          [r["type"] for r in data.get("records") or []], ["A", "CNAME"])
    check("with the block to paste", "CNAME" in (data.get("copy_all") or ""), True)
    check("and where the server IP came from", bool(data.get("server_ip_source")), True)


if __name__ == "__main__":
    test_www_verdicts()
    test_the_list_and_the_copy()
    test_preview_endpoint()
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nDNS RECORDS OK - the list and the verdict agree, and the copy text is the list")
