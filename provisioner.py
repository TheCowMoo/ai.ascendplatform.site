"""
Site provisioning (stage S5).

Turns a ``sites`` row (the desired state) into a live HTTPS site on a target
machine. The target is pluggable:

* **local** — the box the app runs on (the default while testing).
* **remote** — a server from ``ssh_hosts``, driven over SSH.

Everything a target does goes through ``tools.policy`` (what is allowed) and
``actions.gate`` (may it run now, or does a human need to approve it). With
``ALLOW_LIVE_ACTIONS`` unset the whole flow is a *simulation*: it returns exactly
the steps and the URL it would produce, without touching anything. That is what
makes it testable end to end, and safe by default.

Web server: Caddy. Per-site config files are written into ``CADDY_SITES_DIR`` and
the main Caddyfile only needs ``import <dir>/*.caddy``. Caddy then issues the TLS
certificate automatically as soon as the domain resolves here — no certbot.
"""

import html
import json
import os
import socket
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request

import actions
from db import get_connection
from tools import policy
from tools import ssh

# In the compose stack these are shared volumes between the app and Caddy.
SITES_ROOT = (os.environ.get("SITE_ROOT") or "/srv/sites").rstrip("/")
CADDY_SITES_DIR = (os.environ.get("CADDY_SITES_DIR") or "/etc/caddy/sites").rstrip("/")
CADDYFILE = os.environ.get("CADDYFILE") or "/etc/caddy/Caddyfile"
CADDY_RELOAD_CMD = (os.environ.get("CADDY_RELOAD_CMD") or ("caddy reload --config " + CADDYFILE)).strip()
# Where Caddy's admin API answers, when the app runs next to it (the compose stack has no
# caddy binary in the Python image). Set this and a reload is one HTTP POST of CADDYFILE;
# leave it empty for a local install with the caddy CLI, or for a remote target.
CADDY_ADMIN_URL = (os.environ.get("CADDY_ADMIN_URL") or "").strip()
SERVER_IP = (os.environ.get("SERVER_IP") or "").strip()

STEPS = ("dns", "content", "vhost", "reload")


class ProvisionError(Exception):
    """Raised when a target refuses or fails an operation."""


def slug(domain):
    """Lower-cased domain, guaranteed safe to use in a path ('' if invalid)."""
    decision = policy.check_domain(domain)
    return domain.strip().lower() if decision.allowed else ""


# Second-level labels that sit under a country-code TLD. ``example.co.uk`` is a
# registrable name even though it has three labels, so counting dots would call it a
# subdomain and drop the ``www`` row - the one record the user is about to add.
SECOND_LEVEL_LABELS = frozenset((
    "ac", "co", "com", "edu", "go", "gob", "gov", "mil", "ne", "net", "or", "org", "sch",
))


def is_apex(domain):
    """True when ``domain`` is the registrable name, not a host under one.

    This decides whether ``www.`` is part of the site at all. For
    ``test2.example.com`` the answer is no, so asking for a ``www.test2`` record would
    invent a host nobody asked for. There is no public-suffix list in the image, so the
    common two-level suffixes (co.uk, com.au, ...) are named explicitly; everything else
    follows the usual "two labels is the apex" rule.
    """
    labels = [part for part in (domain or "").rstrip(".").lower().split(".") if part]
    if len(labels) < 2:
        return False
    if len(labels) == 2:
        return True
    return (len(labels) == 3 and len(labels[-1]) == 2
            and labels[-2] in SECOND_LEVEL_LABELS)


def docroot_for(domain, sites_root=None):
    root = (sites_root or SITES_ROOT).rstrip("/")
    return "%s/%s/public_html" % (root, slug(domain))


def resolve_domain(domain, timeout=5):
    """All A/AAAA records currently published for a domain."""
    try:
        infos = socket.getaddrinfo(domain, None, proto=socket.IPPROTO_TCP)
    except Exception:
        return []
    addresses = []
    for info in infos:
        address = info[4][0]
        if address not in addresses:
            addresses.append(address)
    return addresses


def resolve_types(domain, timeout=5):
    """(IPv4s, IPv6s) currently published for a domain, in the order DNS returned them."""
    try:
        infos = socket.getaddrinfo(domain, None, proto=socket.IPPROTO_TCP)
    except Exception:
        return [], []
    v4, v6 = [], []
    for info in infos:
        address = info[4][0]
        bucket = v6 if ":" in address else v4
        if address not in bucket:
            bucket.append(address)
    return v4, v6


_IP_CACHE = {"at": 0.0, "ip": ""}
IP_CACHE_SECONDS = 600


def _looks_like_ipv4(value):
    parts = (value or "").strip().split(".")
    if len(parts) != 4:
        return False
    return all(part.isdigit() and 0 <= int(part) <= 255 for part in parts)


def detected_server_ip(timeout=4):
    """This machine's public IPv4, asked of an echo service and cached for ten minutes.

    Only used when neither the settings panel nor ``SERVER_IP`` says what the address is: a
    wrong guess would publish a record nobody can reach, so the value is cached and easy to
    override rather than trusted blindly.
    """
    import time
    import urllib.request

    if _IP_CACHE["ip"] and time.time() - _IP_CACHE["at"] < IP_CACHE_SECONDS:
        return _IP_CACHE["ip"]
    for url in ("https://api.ipify.org", "https://icanhazip.com", "https://ifconfig.me/ip"):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                candidate = resp.read(64).decode("ascii", "ignore").strip()
        except Exception:
            continue
        if _looks_like_ipv4(candidate):
            _IP_CACHE.update({"at": time.time(), "ip": candidate})
            return candidate
    return ""


def server_ip_source():
    """(address, where it came from): 'setting', 'detected', or '' when nobody knows."""
    try:
        import settings as settings_mod
        configured = (settings_mod.get("SERVER_IP") or "").strip()
    except Exception:
        configured = SERVER_IP
    if configured:
        return configured, "setting"
    guess = detected_server_ip()
    return guess, ("detected" if guess else "")


def server_ip():
    return server_ip_source()[0]


CLOUDFLARE_RANGES_URL = "https://www.cloudflare.com/ips-v4"
_CF_CACHE = {"at": 0.0, "nets": []}


def _cloudflare_ranges(timeout=5):
    """Cloudflare's published IPv4 ranges, cached for a day. [] on failure."""
    import time
    import urllib.request

    if _CF_CACHE["nets"] and time.time() - _CF_CACHE["at"] < 86400:
        return _CF_CACHE["nets"]
    try:
        with urllib.request.urlopen(CLOUDFLARE_RANGES_URL, timeout=timeout) as resp:
            body = resp.read(8000).decode("ascii", "ignore")
    except Exception:
        return _CF_CACHE["nets"]
    nets = []
    for line in body.splitlines():
        line = line.strip()
        if "/" not in line:
            continue
        try:
            net, bits = line.split("/")
            nets.append((int.from_bytes(socket.inet_aton(net), "big"), int(bits)))
        except Exception:
            continue
    if nets:
        _CF_CACHE.update({"at": time.time(), "nets": nets})
    return nets


def _in_cloudflare(address):
    if not _looks_like_ipv4(address):
        return False
    nets = _cloudflare_ranges()
    if not nets:
        return False
    value = int.from_bytes(socket.inet_aton(address), "big")
    for net, bits in nets:
        mask = (0xFFFFFFFF << (32 - bits)) & 0xFFFFFFFF
        if value & mask == net & mask:
            return True
    return False


def _record_row(rtype, name, expected, seen, required):
    """One line of the report: what was asked for, what DNS says, and what that means."""
    row = {"type": rtype, "name": name, "value": expected if rtype == "A" else None,
           "seen": list(seen), "required": required, "status": "ok", "note": ""}
    if not expected:
        row["status"] = "unknown"
        row["note"] = "no public IP is configured for this server, so there is nothing to point at"
        return row
    if rtype == "AAAA":
        # We publish no IPv6, so any AAAA is somebody else's answer - and browsers try it first.
        if seen and not all(_in_cloudflare(a) for a in seen):
            row["status"] = "wrong"
            row["note"] = ("IPv6 points somewhere else (%s). Browsers try it first, so the "
                           "site can look broken even when IPv4 is right." % ", ".join(seen[:2]))
        return row
    if not seen:
        row["status"] = "missing"
        row["note"] = ("no record published yet" if required
                       else "optional: add it so www.%s works too" % name)
        return row
    if all(_in_cloudflare(a) for a in seen):
        row["status"] = "proxied"
        row["note"] = ("Cloudflare is proxying this name, so it never resolves to this "
                       "server. Set the record to DNS-only, or install an Origin "
                       "certificate and keep the proxy on.")
        return row
    if expected in seen:
        return row
    row["status"] = "wrong"
    row["note"] = "resolves to %s, expected %s" % (", ".join(seen[:3]), expected)
    return row


def resolve_cname(name, timeout=5):
    """The CNAME target for a name, or "" when the name is not an alias.

    dnspython answers this exactly when it is installed; without it, the canonical name is
    still available from the resolver's own lookup, which is enough to tell "www is an alias
    of the domain" from "www is its own A record".
    """
    bare = (name or "").rstrip(".").lower()
    try:
        import dns.resolver
    except Exception:
        dns = None
    else:
        try:
            answers = dns.resolver.resolve(bare, "CNAME", lifetime=timeout)
            for answer in answers:
                return str(getattr(answer, "target", "") or "").rstrip(".").lower()
            return ""
        except Exception:
            return ""
    try:
        canonical, _, _ = socket.gethostbyname_ex(bare)
    except Exception:
        return ""
    canonical = (canonical or "").rstrip(".").lower()
    return "" if canonical in ("", bare) else canonical


def _www_row(domain, expected):
    """The www verdict, accepting either shape of the record.

    A CNAME is what we recommend, because it keeps working when the server's address changes.
    A site that already published ``A www`` is not *wrong* - it is tomorrow's outage - so it
    passes with a note rather than failing, and nobody has to tear down working DNS.
    """
    name = "www." + domain
    target = resolve_cname(name)
    seen4, _ = resolve_types(name)
    row = {"type": "CNAME", "name": "www", "value": domain, "seen": [], "required": False,
           "status": "ok", "note": "", "alias_of": target}
    if not expected:
        row["status"] = "unknown"
        row["note"] = ("no public IP is configured for this server, so there is nothing to "
                       "point at")
        return row
    if target:
        row["seen"] = [target]
        if target == domain.rstrip(".").lower():
            row["note"] = "CNAME to %s - survives an IP change" % domain
            return row
        row["status"] = "wrong"
        row["note"] = "CNAME points at %s, expected %s" % (target, domain)
        return row
    if not seen4:
        row["status"] = "missing"
        row["note"] = "no record yet: add CNAME www -> %s" % domain
        return row
    if all(_in_cloudflare(address) for address in seen4):
        row["status"] = "proxied"
        row["note"] = ("Cloudflare is proxying this name, so it never resolves to this "
                       "server. Set the record to DNS-only, or install an Origin certificate "
                       "and keep the proxy on.")
        return row
    if expected in seen4:
        row["type"] = "A"
        row["value"] = expected
        row["note"] = ("A record works, but a CNAME www -> %s would survive an IP change"
                       % domain)
        return row
    row["status"] = "wrong"
    row["note"] = "resolves to %s, expected %s" % (", ".join(seen4[:3]), domain)
    return row


def audit_domains(domains, expected=None, report_for=None):
    """Every domain against the address it should point at.

    This answers the question nobody asks until a server move: *which of these domains still
    point at the old address?* It reports what DNS said rather than a verdict, so a domain
    mid-move reads "elsewhere" instead of "broken" - a migration in progress is not a fault.
    """
    expected = expected or server_ip()
    report_for = report_for or dns_report
    out = []
    for domain in domains or []:
        try:
            report = report_for(domain)
        except Exception as exc:                      # a DNS failure is one row, not the sweep
            out.append({"domain": domain, "status": "error", "expected": expected or "",
                        "seen": [], "note": str(exc)[:160], "proxied": False})
            continue
        seen = list(report.get("resolved") or [])
        if not expected:
            status = "unknown"
        elif expected in seen:
            status = "ok"
        elif not seen:
            status = "missing"
        else:
            status = "elsewhere"
        out.append({"domain": domain, "status": status, "expected": expected or "",
                    "seen": seen, "note": report.get("reason") or "",
                    "proxied": bool(report.get("proxied"))})
    return out


def dns_report(domain, server_ip_value=None):
    """Per-record truth for one domain: what to publish, what DNS says, and the verdict.

    One boolean cannot tell you that the apex is right and ``www`` was never created, which
    is the mistake people actually make, so every record gets its own row.
    """
    expected, source = (server_ip_value, "given") if server_ip_value else server_ip_source()
    apex4, apex6 = resolve_types(domain)
    www4, www6 = resolve_types("www." + domain)

    # A subdomain such as test2.example.com has no www of its own: publishing
    # www.test2.example.com would create a host the user never asked for, and failing
    # the report over a record they should not add is how a working domain looks broken.
    rows = [_record_row("A", "@", expected, apex4, True)]
    if is_apex(domain):
        rows.append(_www_row(domain, expected))
    rows.append(_record_row("AAAA", "@", expected, apex6, False))
    if is_apex(domain):
        rows.append(_record_row("AAAA", "www", expected, www6, False))
    required = [row for row in rows if row["required"]]
    proxied = any(row["status"] == "proxied" for row in rows) or (
        bool(apex4) and all(_in_cloudflare(a) for a in apex4))
    ok = bool(expected) and all(row["status"] == "ok" for row in required) and not proxied
    if not expected:
        reason = "no server IP configured"
    elif proxied:
        reason = "proxied by Cloudflare"
    elif ok:
        reason = ""
    elif not apex4:
        reason = "no records published yet"
    else:
        reason = "points elsewhere"
    return {
        "domain": domain,
        "expected": expected or None,
        "expected_source": source,
        "rows": rows,
        "ok": ok,
        "reason": reason,
        "proxied": proxied,
        "resolved": apex4 + apex6,
        "resolved_www": www4 + www6,
    }


def dns_records(domain, server_ip_value=None):
    """The records the user must create at their DNS provider - and the text to copy.

    ``www`` is a CNAME rather than a second A record: it keeps working when the server's
    address changes, which is the difference between a five-minute fix and an outage nobody
    noticed. It is only listed for an apex domain - ``test2.example.com`` is a host in its
    own right and gets one record, not a meaningless ``www.test2``. The ``copy`` strings
    are built here, not in the console, so the button and the table can never disagree
    about what a record is.
    """
    ip = (server_ip_value or server_ip()) or "<this server's public IP>"
    rows = [
        {"type": "A", "name": "@", "host": domain, "value": ip, "ttl": 3600,
         "note": "the site itself"},
    ]
    if is_apex(domain):
        rows.append(
            {"type": "CNAME", "name": "www", "host": "www." + domain, "value": domain,
             "ttl": 3600, "note": "the friendly address"})
    for row in rows:
        row["copy"] = "%s  %s  %s  TTL %d" % (row["type"], row["name"], row["value"],
                                              row["ttl"])
    return rows


def dns_records_text(domain, server_ip_value=None):
    """The same list as one paragraph, for pasting into a ticket or a chat with the client."""
    rows = dns_records(domain, server_ip_value)
    lines = ["DNS records for %s" % domain, ""]
    lines.extend(row["copy"] for row in rows)
    lines.append("")
    lines.append("Add these at the provider where the domain's nameservers point. "
                 "Leave any AAAA records alone unless they point somewhere else.")
    return "\n".join(lines)


def _cert_summary(cert):
    """Issuer, expiry and the names a certificate actually covers."""
    issuer = ""
    for part in (cert or {}).get("issuer") or ():
        for key, value in part:
            if key in ("organizationName", "commonName") and not issuer:
                issuer = value
    names = [value for kind, value in ((cert or {}).get("subjectAltName") or ()) if kind == "DNS"]
    return issuer, (cert or {}).get("notAfter") or "", names


def describe_probe_error(exc):
    """(reason, hint) for a failed HTTPS probe, in words a client can act on."""
    name = type(exc).__name__
    text = str(exc)
    lowered = text.lower()
    if isinstance(exc, ConnectionRefusedError) or "refused" in lowered:
        return (text or name), "Nothing is listening on port 443 - check the firewall and that Caddy is running."
    if "timed out" in lowered or name == "timeout":
        return (text or name), "The connection timed out - the port is filtered, or the server is unreachable."
    if "certificate verify failed" in lowered or name == "SSLCertVerificationError":
        if "hostname mismatch" in lowered or "doesn't match" in lowered:
            return text, "The server answered with a certificate for a different domain."
        if "self-signed" in lowered or "self signed" in lowered:
            return text, "The server is presenting its own internal certificate, so Caddy has not issued one yet."
        return text, "The certificate could not be trusted; it may still be issuing, or Caddy may have failed."
    # Caddy answers an SNI it has a site block for but no certificate for with an
    # "internal error" alert. Saying "not configured" there sent us looking at DNS and
    # the vhost while the real cause was ACME (a rejected contact email, or ports 80/443
    # closed). Name the layer instead of guessing.
    if ("alert internal error" in lowered or "internal error" in lowered
            or "alert_internal_error" in lowered):
        return (text or name), (
            "Caddy accepted the connection but could not present a certificate for this "
            "name: issuance failed or is still running. Check the web server's log for "
            "the ACME error - a rejected ACME contact email (example.com is refused) or "
            "ports 80/443 not reachable are the usual causes.")
    # No certificate at all, rather than a bad one. This is the state between "DNS points
    # here" and "provisioned": Caddy has no site block for the name, so it cannot answer
    # the handshake and the browser reports ERR_SSL_PROTOCOL_ERROR. Saying "the transcript
    # should say why" hid the most common cause of that error.
    if (isinstance(exc, (ConnectionResetError, ConnectionAbortedError))
            or name.startswith("SSL") or "handshake" in lowered
            or "wrong version number" in lowered or "unexpected eof" in lowered
            or "eof occurred" in lowered or "record layer failure" in lowered
            or "connection reset" in lowered):
        return (text or name), (
            "The TLS handshake did not complete because this server is not configured "
            "for the name yet. Provision the site (Domains -> Provision) and make sure "
            "the web server reloaded; a browser shows this as ERR_SSL_PROTOCOL_ERROR.")
    if "name or service not known" in lowered or "getaddrinfo" in lowered or "nodename" in lowered:
        return (text or name), "The domain does not resolve yet - DNS has not propagated to this machine."
    return (text or name), "The HTTPS check failed; the transcript above should say why."


def live_probe(domain, timeout=8, path="/", fetcher=None, now=None):
    """Knock on https://domain and report what actually answered.

    Caddy issues a certificate on the *first handshake* for a name, so this probe is both
    the test and the trigger - nothing else in the platform ever connects to a customer's
    site, which is why "live" used to be a guess. ``fetcher`` exists so tests can supply a
    certificate and status without a network.
    """
    import http.client
    import ssl
    import time

    started = time.time()
    stamp = now or (lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    result = {"domain": domain, "ok": False, "status_code": None, "issuer": "", "expires": "",
              "names": [], "error": "", "hint": "", "seconds": 0.0, "checked_at": stamp()}

    if fetcher is None:
        def fetcher():
            context = ssl.create_default_context()
            raw = socket.create_connection((domain, 443), timeout=timeout)
            with context.wrap_socket(raw, server_hostname=domain) as tls:
                cert = tls.getpeercert()
                tls.close()
            # A second, cheap request: the handshake above may have been the very thing
            # that made Caddy go and fetch the certificate.
            connection = http.client.HTTPSConnection(domain, 443, timeout=timeout,
                                                     context=ssl.create_default_context())
            connection.request("GET", path)
            response = connection.getresponse()
            status = response.status
            response.read(2048)
            connection.close()
            return status, cert

    try:
        status, cert = fetcher()
    except Exception as exc:
        result["error"], result["hint"] = describe_probe_error(exc)
        result["seconds"] = round(time.time() - started, 1)
        return result

    issuer, expires, names = _cert_summary(cert)
    result.update({"status_code": status, "issuer": issuer, "expires": expires, "names": names})
    result["seconds"] = round(time.time() - started, 1)
    if status in (200, 301, 302, 307, 308):
        result["ok"] = True
        return result
    if status in (521, 522, 523, 524, 526):
        result["hint"] = ("A proxy in front of this server answered %d - Cloudflare proxying "
                          "blocks the certificate challenge." % status)
    else:
        result["hint"] = "The site answered %d; the certificate is installed but the page is not healthy." % status
    result["error"] = "HTTP %d" % status
    return result


def check_dns(domain, server_ip=None):
    """The one-line verdict, kept for callers that only need ok/reason.

    It is now a summary of :func:`dns_report`, so the console and the provisioning job can
    never disagree about whether a domain points here.
    """
    report = dns_report(domain, server_ip)
    return {
        "domain": domain,
        "resolved": report["resolved"],
        "expected": report["expected"],
        "ok": report["ok"],
        "reason": report["reason"],
    }


VHOST_TEMPLATE = """# {domain} - managed by CMBGrowthAI (do not edit by hand)
{hosts} {{
	encode zstd gzip
{body}}}
"""

# The form page and the two endpoints its script posts to live in the app. Everything
# else on a client's domain stays a plain static file - publishing a page must never make
# the whole site depend on the Python process.
FORMS_HANDLER = """	@forms path /f/* /api/public/forms/*
	handle @forms {{
		reverse_proxy {upstream} {{
			header_up X-Real-IP {{remote_host}}
		}}
	}}
	handle {{
		root * {docroot}
		file_server
	}}
"""

STATIC_HANDLER = """	root * {docroot}
	file_server
"""


INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{tagline}">
<style>
  :root {{
    --brand: {color};
    --ink: {ink};
    --soft: {soft};
    --line: #e6e8ec;
    --text: #1f2937;
    --muted: #5b6472;
  }}
  * {{ box-sizing: border-box; }}
  html {{ -webkit-text-size-adjust: 100%; }}
  body {{
    margin: 0; font-family: {font};
    color: var(--text); background: #fff;
    font-size: 17px; line-height: 1.65;
    -webkit-font-smoothing: antialiased;
  }}
  .wrap {{ max-width: 760px; margin: 0 auto; padding: 0 24px; }}

  /* The brand as an accent bloom, not a full-bleed wash: a saturated block of a
     client's colour is the quickest way for a page to look unfinished. */
  header {{ position: relative; padding: 96px 0 76px; text-align: center; overflow: hidden; border-bottom: 1px solid var(--line); }}
  header::before {{
    content: ""; position: absolute; left: 50%; top: -60%; width: 620px; height: 620px;
    transform: translateX(-50%); border-radius: 50%;
    background: var(--soft); filter: blur(70px); z-index: 0;
  }}
  header .wrap {{ position: relative; z-index: 1; }}
  .eyebrow {{
    display: inline-flex; align-items: center; gap: 8px; margin-bottom: 22px;
    padding: 6px 14px; border-radius: 999px;
    background: #fff; border: 1px solid var(--line);
    font-size: 12px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase;
    color: var(--muted);
  }}
  .eyebrow::before {{ content: ""; width: 7px; height: 7px; border-radius: 50%; background: var(--brand); }}
  h1 {{
    margin: 0 0 14px; font-size: 46px; line-height: 1.1;
    letter-spacing: -0.03em; font-weight: 680; color: #0b1120;
  }}
  header p {{ margin: 0; font-size: 19px; color: var(--muted); max-width: 44ch; margin: 0 auto; }}

  main {{ padding: 56px 0 24px; }}
  h2 {{
    position: relative; margin: 40px 0 14px; padding-left: 16px;
    font-size: 22px; letter-spacing: -0.02em; font-weight: 650; color: #0b1120;
  }}
  h2:first-child {{ margin-top: 0; }}
  h2::before {{
    content: ""; position: absolute; left: 0; top: .28em; bottom: .22em;
    width: 3px; border-radius: 2px; background: var(--brand);
  }}
  p {{ margin: 0 0 16px; }}
  a {{ color: var(--brand); }}
  ul {{ padding-left: 20px; }}
  li {{ margin-bottom: 6px; }}
  blockquote {{ margin: 0 0 16px; padding: 2px 0 2px 16px; border-left: 3px solid var(--soft); color: var(--muted); }}
  code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .9em; background: #f6f7f9; padding: 2px 6px; border-radius: 6px; }}
  img {{ max-width: 100%; height: auto; border-radius: 12px; }}
  hr {{ border: none; border-top: 1px solid var(--line); margin: 32px 0; }}

  footer {{ margin-top: 56px; border-top: 1px solid var(--line); padding: 28px 0 40px; text-align: center; color: var(--muted); font-size: 13px; }}
  footer a {{ color: var(--muted); text-decoration: none; border-bottom: 1px solid var(--line); }}

  @media (max-width: 560px) {{
    header {{ padding: 64px 0 52px; }}
    h1 {{ font-size: 32px; }}
    header p {{ font-size: 17px; }}
    main {{ padding: 40px 0 16px; }}
  }}
</style>
</head>
<body>
<header>
  <div class="wrap">
    <span class="eyebrow">{brand}</span>
    <h1>{title}</h1>
    <p>{tagline}</p>
  </div>
</header>
<main>
  <div class="wrap">
{body}
  </div>
</main>
<footer>
  <div class="wrap">Built with <a href="https://ascendplatform.site">{brand}</a></div>
</footer>
</body>
</html>
"""


SAFE_FONTS = (
    "system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
    "Georgia, 'Times New Roman', serif",
    "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
)


def _rgb(hex_color):
    """Parse #rgb or #rrggbb into (r, g, b), or None."""
    value = (hex_color or "").strip().lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if len(value) != 6:
        return None
    try:
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def readable_ink(hex_color):
    """``#0b1120`` or ``#ffffff`` - whichever is legible on this background.

    The brand colour is a client's choice, and pale ones exist: white text on
    #fde68a is unreadable, so the foreground is computed rather than assumed.
    Same threshold as the widget's ``inkOn`` so both surfaces agree.
    """
    rgb = _rgb(hex_color)
    if not rgb:
        return "#ffffff"

    def lin(channel):
        c = channel / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    luminance = 0.2126 * lin(rgb[0]) + 0.7152 * lin(rgb[1]) + 0.0722 * lin(rgb[2])
    return "#0b1120" if luminance > 0.42 else "#ffffff"


def soft_tint(hex_color, alpha=0.16):
    """The brand at low alpha, so tints follow the client's own hue."""
    rgb = _rgb(hex_color) or (79, 70, 229)
    return "rgba(%d,%d,%d,%.2f)" % (rgb[0], rgb[1], rgb[2], alpha)


def safe_font(value):
    """Whitelist a font stack.

    The stack comes from a client-editable profile field and is interpolated into a
    ``<style>`` block, so passing it through untouched would let a client's value
    close the rule and inject its own CSS. A bare family name is accepted; anything
    else falls back to the system stack.
    """
    text = (value or "").strip()
    for choice in SAFE_FONTS:
        if text.lower() == choice.lower():
            return choice
    if text and all(ch.isalnum() or ch in " -_" for ch in text):
        return '"%s", %s' % (text, SAFE_FONTS[0])
    return SAFE_FONTS[0]


def _brand_name():
    """The product name, honouring the Settings tab when it is readable."""
    try:
        import settings
        return settings.get("BRAND_NAME", "AscendPlatform") or "AscendPlatform"
    except Exception:
        return "AscendPlatform"


def html_escape(value):
    return html.escape(value or "")


def platform_base():
    """The public origin of the platform, without a trailing slash ('' if unset).

    ``PLATFORM_URL`` is the operator's explicit choice. The compose stack also knows
    ``ADMIN_DOMAIN`` (Caddy uses it), so it is a useful fallback when the panel value
    was never filled in. A loopback name is not a public origin for a remote site.
    """
    try:
        import settings as settings_mod

        base = (settings_mod.get("PLATFORM_URL") or "").strip()
        if not base:
            domain = (settings_mod.get("ADMIN_DOMAIN")
                      or os.environ.get("ADMIN_DOMAIN") or "").strip()
            if domain.lower() in ("localhost", "127.0.0.1", "::1"):
                domain = ""
            if domain:
                base = domain if "://" in domain else "https://" + domain
        return base.rstrip("/")
    except Exception:
        return ""


def _safe_upstream(value):
    """A Caddy upstream address we are willing to write into a site block, or ''.

    ``FORMS_UPSTREAM`` is operator input, but a site block is a config file: a newline
    or a brace there would let it inject directives. Restricting the alphabet keeps the
    value to the shapes Caddy actually accepts (host:port, https://host, [v6]:port).
    """
    text = (value or "").strip().rstrip("/")
    if not text or len(text) > 300:
        return ""
    if not all(ch.isalnum() or ch in ".:-_/[]" for ch in text):
        return ""
    return text


def forms_upstream_for(target=None):
    """Where a client's ``/f/*`` and ``/api/public/forms/*`` requests should go.

    A local site is served by Caddy in the compose network, where the app answers at
    ``app:8000``. A site on a separate server cannot resolve that name, so it proxies
    to the platform's public origin instead. An explicit ``FORMS_UPSTREAM`` wins for
    either (a host install of Caddy usually wants ``127.0.0.1:8000``).
    """
    override = _safe_upstream(os.environ.get("FORMS_UPSTREAM") or "")
    if override:
        return override
    if target is not None and getattr(target, "is_remote", False):
        return _safe_upstream(platform_base())
    return _safe_upstream("app:8000")


def _vhost_body(docroot, upstream=None):
    """The directives inside a site block: form proxy first, then static files."""
    if upstream:
        return FORMS_HANDLER.format(upstream=upstream, docroot=docroot)
    return STATIC_HANDLER.format(docroot=docroot)


def render_vhost(domain, docroot, aliases=None, forms_upstream=None):
    """Caddy site block for a domain plus its aliases.

    Duplicates are dropped: the apex block adds ``www`` when it resolves, and a site that
    already listed it would otherwise produce the same host twice - which Caddy rejects
    as a duplicate site address, turning a harmless overlap into a failed reload.
    """
    hosts = []
    for host in [slug(domain)] + [slug(a) for a in (aliases or []) if a]:
        if host and host not in hosts:
            hosts.append(host)
    if not hosts:
        raise ProvisionError("no valid host for the site block")
    return VHOST_TEMPLATE.format(domain=slug(domain), hosts=", ".join(hosts),
                                 body=_vhost_body(docroot, forms_upstream))


def render_index(title="New site", tagline="", body_html="", brand_color="#4f46e5",
                 font_family="system-ui, sans-serif"):
    """A small, self-contained landing page used when no content is supplied.

    Everything is inline because the result is written into a client's own docroot
    and served by Caddy with no build step: no stylesheet request, no webfont.
    """
    color = brand_color or "#4f46e5"
    return INDEX_TEMPLATE.format(
        title=html_escape(title),
        tagline=html_escape(tagline),
        body=body_html or "<p>This site is ready. Add content in the Sites tab.</p>",
        color=color,
        # Derived from the brand: a pale colour needs dark ink, and the tint has to
        # follow the hue or the hero bloom reads as a grey smudge.
        ink=readable_ink(color),
        soft=soft_tint(color, 0.16),
        font=safe_font(font_family),
        brand=html_escape(_brand_name()),
    )


STARTER_TEXT = "This site is ready. Add content in the Sites tab."


def is_starter_content(content):
    """True for the placeholder a newly provisioned site serves.

    The first home-page publish may replace this without a confirmation: it is not a
    page the user wrote. A publish over any other existing content needs one.
    """
    return STARTER_TEXT in (content or "")


def _services_from_profile(profile):
    raw = (profile or {}).get("services")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "[]")
        except Exception:
            raw = []
    rendered = []
    for item in raw or []:
        if isinstance(item, dict) and (item.get("name") or item.get("description")):
            price = " - %s" % html_escape(item.get("price")) if item.get("price") else ""
            rendered.append(
                "<li><strong>%s</strong>%s<br>%s</li>"
                % (html_escape(item.get("name")), price, html_escape(item.get("description")))
            )
    return rendered


def index_from_profile(profile, site=None):
    """Build a landing page from the Business Profile (used by the agent)."""
    profile = profile or {}
    site = site or {}
    name = profile.get("name") or site.get("domain") or "New site"
    parts = []
    if profile.get("description"):
        parts.append("<p>%s</p>" % html_escape(profile["description"][:1500]))
    services = _services_from_profile(profile)
    if services:
        parts.append("<h2>What we do</h2><ul>%s</ul>" % "".join(services))
    if profile.get("support_email") or profile.get("support_phone"):
        parts.append(
            "<h2>Contact</h2><p>%s %s</p>"
            % (html_escape(profile.get("support_email")), html_escape(profile.get("support_phone")))
        )
    return render_index(
        title=name,
        tagline=profile.get("tagline") or "",
        body_html="\n".join(parts),
        brand_color=profile.get("brand_color") or "#4f46e5",
        font_family=profile.get("brand_font") or "system-ui, sans-serif",
    )


def join_path(base, *parts):
    """POSIX-style path join (targets are Linux; keeps vhost paths predictable)."""
    pieces = [str(base).rstrip("/")] + [str(p).strip("/") for p in parts if p]
    return "/".join(pieces)


def _aliases(site):
    raw = (site or {}).get("aliases")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "[]")
        except Exception:
            raw = []
    return [a for a in (raw or []) if a]


def _vhost_hosts(content):
    """The host names already in a site block, so a refresh does not drop ``www``.

    The database's ``aliases`` column is the source of truth for new sites, but older
    rows were created before it existed and their ``www`` lives only in the file. A
    config-only rewrite must be additive: losing a working host to gain forms is not a
    refresh, it is a regression.
    """
    for line in (content or "").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if not text.endswith("{"):
            return []
        hosts = []
        for part in text[:-1].strip().split(","):
            host = part.strip().lower()
            if "://" in host:
                host = host.split("://", 1)[1]
            host = host.split("/", 1)[0].strip()
            if slug(host) and host not in hosts:
                hosts.append(host)
        return hosts
    return []


def _authorise(tool, summary, args, job_id, emit, command=None, allow_mutating=True,
               preapproved=False):
    """Policy first, then the approval gate. Shared by both targets.

    ``preapproved`` is set by a caller that already asked for one approval covering a
    whole run - provisioning does, so a site is not three separate approvals that can be
    missed. Policy is still enforced: approval to publish a site is not approval to run
    any command at all. Dry-run still goes through the gate, so it keeps its note.
    """
    if command is not None:
        decision = policy.check_command(command, allow_mutating=allow_mutating)
        if not decision.allowed:
            if emit:
                emit("policy", "error", "Blocked by policy: %s (%s)" % (command, decision.reason))
            return {"allowed": False, "blocked": True, "dry_run": actions.is_dry_run(),
                    "reason": decision.reason}
    if preapproved and not actions.is_dry_run():
        if emit:
            emit("gate", "system", "Covered by the operator's approval: %s" % summary)
        return {"allowed": True, "dry_run": False, "reason": "preapproved", "approval_id": None}
    return actions.gate(tool, summary, args, job_id, emit)


def _admin_url_ok(url):
    try:
        parts = urllib.parse.urlsplit(url)
    except Exception:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def reload_web_server(target, job_id=None, emit=None, preapproved=False):
    """Ask the web server to re-read its configuration.

    In the compose stack the app runs in a Python image with no caddy binary, so the
    reload is an HTTP POST of the project's own Caddyfile to Caddy's admin API. It used
    to be a shell string, which put a fixed platform action behind the *agent* command
    policy - and that policy refuses ``curl`` (correctly: an agent could POST anywhere).
    Every live provisioning run then wrote the site block and failed here, and a domain
    with a perfect vhost answered the browser with a TLS alert because Caddy had never
    read it.

    The destination comes from ``CADDY_ADMIN_URL`` and the body is the Caddyfile the
    container already mounts, so this is not an agent-chosen request. A remote target
    keeps its own reload command: the admin API is not reachable from there.
    """
    if target.is_remote or not CADDY_ADMIN_URL:
        return target.run(CADDY_RELOAD_CMD, job_id, emit, allow_mutating=True,
                          summary="reload the web server (%s)" % target.name,
                          preapproved=preapproved)

    summary = "reload the web server via %s" % CADDY_ADMIN_URL
    if preapproved and not actions.is_dry_run():
        if emit:
            emit("gate", "system", "Covered by the operator's approval: %s" % summary)
    else:
        authorisation = actions.gate("caddy_reload", summary, {"url": CADDY_ADMIN_URL},
                                     job_id, emit)
        if not authorisation["allowed"]:
            return {"ok": False, "dry_run": authorisation["dry_run"],
                    "output": authorisation["reason"]}
        if authorisation["dry_run"]:
            return {"ok": True, "dry_run": True,
                    "output": "[dry-run] would POST %s to %s/load"
                              % (CADDYFILE, CADDY_ADMIN_URL)}

    if not _admin_url_ok(CADDY_ADMIN_URL):
        return {"ok": False, "dry_run": False,
                "output": "CADDY_ADMIN_URL must be an http(s) URL, got %r" % CADDY_ADMIN_URL}
    try:
        with open(CADDYFILE, "rb") as handle:
            body = handle.read()
    except OSError as exc:
        return {"ok": False, "dry_run": False,
                "output": "cannot read %s: %s" % (CADDYFILE, exc)}

    request = urllib.request.Request(
        CADDY_ADMIN_URL.rstrip("/") + "/load", data=body, method="POST",
        headers={"Content-Type": "text/caddyfile"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = int(getattr(response, "status", 200))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read()[:2000].decode("utf-8", "replace").strip()
        except Exception:
            detail = ""
        return {"ok": False, "dry_run": False,
                "output": "Caddy rejected the config (HTTP %s): %s"
                          % (exc.code, detail or "no detail")}
    except Exception as exc:
        return {"ok": False, "dry_run": False,
                "output": "%s: %s" % (type(exc).__name__, exc)}
    return {"ok": 200 <= status < 300, "dry_run": False,
            "output": "reloaded Caddy via %s (HTTP %s)" % (CADDY_ADMIN_URL, status)}


class LocalTarget:
    """The machine the app itself runs on."""

    name = "local"
    is_remote = False

    def __init__(self, sites_root=None, caddy_sites_dir=None):
        self.sites_root = (sites_root or SITES_ROOT).rstrip("/")
        self.caddy_sites_dir = (caddy_sites_dir or CADDY_SITES_DIR).rstrip("/")
        self.allowed_root = self.sites_root

    def run(self, command, job_id=None, emit=None, allow_mutating=True, summary=None,
            preapproved=False):
        authorisation = _authorise("shell", summary or command, {"command": command},
                                   job_id, emit, command=command, allow_mutating=allow_mutating,
                                   preapproved=preapproved)
        if not authorisation["allowed"]:
            return {"ok": False, "blocked": authorisation.get("blocked", False),
                    "dry_run": authorisation["dry_run"], "output": authorisation["reason"]}
        if authorisation["dry_run"]:
            return {"ok": True, "dry_run": True, "output": "[dry-run] would run: %s" % command}
        try:
            completed = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=180)
        except Exception as exc:
            return {"ok": False, "dry_run": False, "output": "%s: %s" % (type(exc).__name__, exc)}
        output = (completed.stdout or "") + (completed.stderr or "")
        return {"ok": completed.returncode == 0, "dry_run": False,
                "output": output.strip()[:4000], "exit_code": completed.returncode}

    def read_file(self, path, limit=200000):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                return handle.read(limit)
        except Exception:
            return ""

    def write_file(self, path, content, job_id=None, emit=None, summary=None,
                   preapproved=False):
        decision = check_write_path(self, path)
        if not decision.allowed:
            if emit:
                emit("policy", "error", "Refused to write %s: %s" % (path, decision.reason))
            return {"ok": False, "blocked": True, "dry_run": actions.is_dry_run(),
                    "output": "policy: %s" % decision.reason}
        authorisation = _authorise("file_write", summary or ("write %s" % path),
                                   {"path": path, "bytes": len(content)}, job_id, emit,
                                   preapproved=preapproved)
        if not authorisation["allowed"]:
            return {"ok": False, "blocked": False, "dry_run": authorisation["dry_run"],
                    "output": authorisation["reason"]}
        if authorisation["dry_run"]:
            return {"ok": True, "dry_run": True,
                    "output": "[dry-run] would write %d bytes to %s" % (len(content), path)}
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(content)
        except Exception as exc:
            return {"ok": False, "dry_run": False, "output": "%s: %s" % (type(exc).__name__, exc)}
        return {"ok": True, "dry_run": False, "output": "wrote %d bytes to %s" % (len(content), path)}


class RemoteTarget:
    """A server from the ``ssh_hosts`` table, driven over SSH."""

    name = "remote"
    is_remote = True

    def __init__(self, host_row):
        self.host = dict(host_row)
        self.sites_root = (host_row.get("sites_root") or host_row.get("allowed_root") or SITES_ROOT).rstrip("/")
        self.allowed_root = (host_row.get("allowed_root") or self.sites_root).rstrip("/")
        self.caddy_sites_dir = CADDY_SITES_DIR

    def label(self):
        return "%s@%s" % (self.host.get("username") or "?", self.host.get("host") or "?")

    def _ssh(self, command):
        result = ssh.run(self.host, command)
        return {
            "ok": result["exit_code"] == 0,
            "dry_run": False,
            "exit_code": result["exit_code"],
            "output": (result["stdout"] or result["stderr"]).strip()[:4000],
        }

    def run(self, command, job_id=None, emit=None, allow_mutating=True, summary=None,
            preapproved=False):
        authorisation = _authorise(
            "ssh",
            summary or ("%s: %s" % (self.label(), command)),
            {"host": self.label(), "command": command},
            job_id, emit, command=command, allow_mutating=allow_mutating,
            preapproved=preapproved,
        )
        if not authorisation["allowed"]:
            return {"ok": False, "blocked": authorisation.get("blocked", False),
                    "dry_run": authorisation["dry_run"], "output": authorisation["reason"]}
        if authorisation["dry_run"]:
            return {"ok": True, "dry_run": True,
                    "output": "[dry-run] would run on %s: %s" % (self.label(), command)}
        try:
            return self._ssh(command)
        except Exception as exc:
            return {"ok": False, "dry_run": False, "output": "%s: %s" % (type(exc).__name__, exc)}

    def read_file(self, path, limit=200000):
        try:
            result = ssh.run(self.host, "cat %s" % policy.quote(path))
            return (result["stdout"] or "")[:limit]
        except Exception:
            return ""

    def write_file(self, path, content, job_id=None, emit=None, summary=None,
                   preapproved=False):
        decision = check_write_path(self, path)
        if not decision.allowed:
            if emit:
                emit("policy", "error", "Refused to write %s: %s" % (path, decision.reason))
            return {"ok": False, "blocked": True, "dry_run": actions.is_dry_run(),
                    "output": "policy: %s" % decision.reason}

        authorisation = _authorise(
            "ssh_write",
            summary or ("write %s on %s (%d bytes)" % (path, self.label(), len(content))),
            {"host": self.label(), "path": path, "bytes": len(content)},
            job_id, emit, preapproved=preapproved,
        )
        if not authorisation["allowed"]:
            return {"ok": False, "blocked": False, "dry_run": authorisation["dry_run"],
                    "output": authorisation["reason"]}
        if authorisation["dry_run"]:
            return {"ok": True, "dry_run": True,
                    "output": "[dry-run] would upload %d bytes to %s on %s"
                              % (len(content), path, self.label())}

        temp_path = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".tmp", delete=False, encoding="utf-8") as handle:
                handle.write(content)
                temp_path = handle.name
            # mkdir + upload are the same already-approved action, so the low-level
            # transport is used directly rather than raising a second approval.
            prepared = self._ssh("mkdir -p %s" % policy.quote(os.path.dirname(path)))
            if not prepared["ok"]:
                return prepared
            ssh.put_file(self.host, temp_path, path)
            return {"ok": True, "dry_run": False, "output": "uploaded %d bytes to %s" % (len(content), path)}
        except Exception as exc:
            return {"ok": False, "dry_run": False, "output": "%s: %s" % (type(exc).__name__, exc)}
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)


def write_roots(target):
    """Roots a target may write into: site content and the web-server config dir."""
    return [root for root in (getattr(target, "sites_root", None),
                              getattr(target, "caddy_sites_dir", None)) if root]


def check_write_path(target, path):
    """Confine a write to either the site root or the vhost config directory."""
    decisions = [policy.check_path(path, root) for root in write_roots(target)]
    for decision in decisions:
        if decision.allowed:
            return decision
    return decisions[0] if decisions else policy.check_path(path, "")


# ---------------------------------------------------------------------------
# Target selection
# ---------------------------------------------------------------------------
def find_host(identifier):
    """Look up an ``ssh_hosts`` row by id or label."""
    value = str(identifier or "").strip()
    if not value:
        return None
    with get_connection() as conn:
        if value.isdigit():
            row = conn.execute("SELECT * FROM ssh_hosts WHERE id = ?", (int(value),)).fetchone()
        else:
            row = conn.execute("SELECT * FROM ssh_hosts WHERE label = ?", (value,)).fetchone()
    return {k: row[k] for k in row.keys()} if row else None


def get_target(site=None):
    """Resolve the site's target: 'local' (default) or an ssh_hosts id/label."""
    value = str((site or {}).get("target") or "local").strip()
    if value.lower() in ("", "local", "self"):
        return LocalTarget()
    host = find_host(value)
    if not host:
        raise ProvisionError("unknown target %r: add it in the Servers tab first" % value)
    if not host.get("enabled"):
        raise ProvisionError("target %r is disabled" % value)
    return RemoteTarget(host)


def target_docroot(site):
    target = get_target(site)
    domain = slug((site or {}).get("domain"))
    docroot = (site.get("docroot") or "").strip() or docroot_for(domain, target.sites_root)
    return target, docroot


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------
def provision(site, profile=None, job_id=None, emit=None, server_ip=None, preapproved=False):
    """Make the target match the site row.

    Returns ``url``, ``status`` and per-step detail. In dry-run everything is
    simulated, so the returned URL is exactly what a live run would publish.

    ``preapproved`` is set when the caller already obtained one approval for the whole
    run (the ``site_provision`` job does). Without it, each write and the reload asks
    its own question - three prompts for one intent, and missing the third leaves a
    configured site with no certificate.
    """
    domain = slug((site or {}).get("domain"))
    if not domain:
        return {"ok": False, "error": "invalid or missing domain", "steps": [],
                "url": "", "status": "error", "target": "local"}

    target, docroot = target_docroot(site)
    steps = []

    def record(name, result, detail=None):
        text = detail if detail is not None else result.get("output", "")
        steps.append({"step": name, "ok": bool(result.get("ok")), "detail": text})
        if emit:
            emit("provision", "system", "[%s] %s: %s" % (target.name, name, text))

    report = dns_report(domain, server_ip)
    dns = {"domain": domain, "resolved": report["resolved"], "expected": report["expected"],
           "ok": report["ok"], "reason": report["reason"]}
    record("dns", {"ok": dns["ok"]},
           "resolved=%s expected=%s %s" % (dns["resolved"], dns["expected"], dns["reason"]))

    content = (site.get("content_html") or "").strip() or index_from_profile(profile, site)
    record("content", target.write_file(join_path(docroot, "index.html"), content, job_id, emit,
                                        summary="write index.html for %s" % domain,
                                        preapproved=preapproved))

    upstream = forms_upstream_for(target)
    aliases = _aliases(site)
    if is_apex(domain):
        # www is optional, but when it does point here it has to be in the same site
        # block: otherwise a browser asking for www gets the apex certificate and
        # fails, and the panel that told the user to add the record looks wrong.
        points_here = any(row["name"] == "www" and row["type"] in ("CNAME", "A")
                          and row["status"] == "ok" for row in report["rows"])
        if points_here:
            aliases.append("www." + domain)
    vhost = render_vhost(domain, docroot, aliases, forms_upstream=upstream)
    record("vhost", target.write_file(join_path(target.caddy_sites_dir, domain + ".caddy"), vhost,
                                      job_id, emit,
                                      summary="install the web server config for %s" % domain,
                                      preapproved=preapproved))

    record("reload", reload_web_server(target, job_id, emit, preapproved=preapproved))

    # Certificate. Caddy issues on the first TLS handshake for a name, so this probe is
    # both the request and the proof - without it a job could report "live" while the
    # browser got a name-mismatch error. Retried because ACME validation takes seconds.
    import time

    ssl_probe = {"ok": False, "error": "not attempted", "hint": "", "issuer": "",
                 "expires": "", "status_code": None}
    if actions.is_dry_run():
        ssl_probe = {"ok": True, "error": "", "hint": "", "issuer": "dry-run",
                     "expires": "", "status_code": None}
        record("ssl", {"ok": True}, "[dry-run] would request and verify the certificate")
    else:
        for attempt in range(18):
            ssl_probe = live_probe(domain, timeout=6)
            if ssl_probe["ok"]:
                break
            if emit and attempt % 3 == 0:
                emit("provision", "system", "[ssl] requesting certificate… attempt %d/18"
                     % (attempt + 1))
            time.sleep(5)
        detail = ("issued by %s, expires %s" % (ssl_probe["issuer"], ssl_probe["expires"])
                  if ssl_probe["ok"] else (ssl_probe["hint"] or ssl_probe["error"]))
        record("ssl", {"ok": ssl_probe["ok"]}, detail)

    # DNS is the user's job, so unpublished records are the expected state before
    # they create them — not a failure. `ok` reflects whether the *target* was
    # configured successfully; `status` distinguishes waiting-for-DNS from live.
    configured = all(step["ok"] for step in steps if step["step"] not in ("dns", "ssl"))
    # "live" now needs a certificate that was actually served, not just a vhost that was
    # written: a site can be perfectly configured and still fail in the browser.
    status = "error" if not configured else (
        "live" if (dns["ok"] and ssl_probe["ok"]) else "pending_dns")
    return {
        "ok": configured,
        "dry_run": actions.is_dry_run(),
        "target": target.name,
        "url": ("https://" + domain) if configured else "",
        "status": status,
        "docroot": docroot,
        "steps": steps,
        "dns": dns,
    }


def refresh_vhost(site, actor="", job_id=None, emit=None):
    """Rewrite one site's web-server block without touching its content.

    This is the "make forms work on the domain I already published" action. It reads the
    existing vhost only to preserve hosts (``www`` on older rows), re-renders the block
    with the form proxy, writes that one file, and reloads Caddy. ``index.html`` and the
    database's page content are deliberately absent from this function.

    The click is the approval: in live mode one approved ``file_write`` row names the
    vhost, and the write and reload run ``preapproved`` so a synchronous Flask handler
    never blocks the event loop waiting for a second decision.
    """
    domain = slug((site or {}).get("domain"))
    if not domain:
        return {"ok": False, "error": "invalid or missing domain", "steps": []}

    target, docroot = target_docroot(site)
    vhost_path = join_path(target.caddy_sites_dir, domain + ".caddy")
    upstream = forms_upstream_for(target)
    if not upstream:
        return {
            "ok": False, "steps": [],
            "error": ("Set PLATFORM_URL (or FORMS_UPSTREAM) so forms can be served on "
                      "%s. Nothing was changed." % domain),
        }

    aliases = _aliases(site)
    for host in _vhost_hosts(target.read_file(vhost_path)):
        if host != domain and host not in aliases:
            aliases.append(host)
    vhost = render_vhost(domain, docroot, aliases, forms_upstream=upstream)
    summary = "refresh the web server config for %s (forms on /f/*)" % domain

    live = not actions.is_dry_run()
    approval_id = None
    if live:
        approval_id = actions.request_approval(
            "file_write", summary,
            {"path": vhost_path, "forms_upstream": upstream}, None)
        decided = actions.decide_approval(approval_id, "approved", actor or "operator")
        if not decided or decided.get("status") != "approved":
            return {"ok": False, "steps": [],
                    "error": ("This refresh could not be recorded as approved "
                              "(approval #%s)." % approval_id)}

    steps = []
    written = target.write_file(vhost_path, vhost, None, emit, summary=summary,
                                preapproved=live)
    steps.append({"step": "vhost", "ok": bool(written.get("ok")),
                  "detail": written.get("output", "")})
    if approval_id:
        actions.set_result(approval_id, written.get("output")
                           or ("wrote to %s" % vhost_path if written.get("ok")
                               else "the write failed"))

    reloaded = {"ok": False, "output": "not attempted"}
    if written.get("ok"):
        reloaded = reload_web_server(target, None, emit, preapproved=live)
    steps.append({"step": "reload", "ok": bool(reloaded.get("ok")),
                  "detail": reloaded.get("output", "")})

    return {
        "ok": all(step["ok"] for step in steps),
        "dry_run": actions.is_dry_run(),
        "target": target.name,
        "path": vhost_path,
        "url": "https://" + domain,
        "forms_url": "https://%s/f/" % domain,
        "upstream": upstream,
        "steps": steps,
    }


def remove_site(site, job_id=None, emit=None, preapproved=False):
    """Remove the web-server config for a site (content is left in place).

    The document root is deliberately untouched. Disconnecting a domain is reversible
    at the file level: the client can reconnect it and their pages are still there.
    ``preapproved`` is set by a console click that already recorded the decision, so a
    synchronous Flask handler never waits on the approval gate.
    """
    domain = slug((site or {}).get("domain"))
    if not domain:
        return {"ok": False, "error": "invalid or missing domain", "steps": []}
    target = get_target(site)
    steps = []
    vhost_path = join_path(target.caddy_sites_dir, domain + ".caddy")
    result = target.run("rm -f %s" % policy.quote(vhost_path), job_id, emit, allow_mutating=True,
                        summary="remove the web server config for %s" % domain,
                        preapproved=preapproved)
    steps.append({"step": "remove_vhost", "ok": result["ok"], "detail": result.get("output", "")})
    if result["ok"]:
        reload_result = reload_web_server(target, job_id, emit, preapproved=preapproved)
    else:
        reload_result = {"ok": False, "output": "not attempted: the config was not removed"}
    steps.append({"step": "reload", "ok": reload_result["ok"], "detail": reload_result.get("output", "")})
    return {"ok": all(step["ok"] for step in steps), "dry_run": actions.is_dry_run(),
            "path": vhost_path, "steps": steps}


# ---------------------------------------------------------------------------
# Command line (for the VPS)
# ---------------------------------------------------------------------------
def main(argv=None):
    """``python -m provisioner check <domain>`` - the same verdict the console shows.

    The question this answers is the one that arrives as a screenshot:
    ``ERR_SSL_PROTOCOL_ERROR``. It prints every record with its verdict, then knocks on
    HTTPS with the same probe provisioning uses, so "DNS is right but nothing is
    configured" is visible without opening the UI.
    """
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print("usage: python -m provisioner check <domain>")
        return 0
    if args[0] != "check" or len(args) < 2:
        print("usage: python -m provisioner check <domain>")
        return 2

    domain = slug(args[1])
    if not domain:
        print("not a valid domain: %s" % args[1])
        return 2

    report = dns_report(domain)
    print("domain:    %s" % domain)
    print("server ip: %s (%s)" % (report["expected"] or "not set", report["expected_source"]))
    if not report["expected"]:
        print("           set SERVER_IP so there is an address to point at")
    for row in report["rows"]:
        seen = ", ".join(row["seen"]) or "-"
        print("  %-5s %-4s %-40s %-8s %s" % (row["type"], row["name"], seen,
                                              row["status"], row["note"] or ""))
    print("dns:       %s" % ("ok" if report["ok"] else (report["reason"] or "not ok")))

    probe = live_probe(domain)
    if probe["ok"]:
        print("https:     ok - %s, expires %s" % (probe["issuer"] or "issued",
                                                  probe["expires"] or "unknown"))
    else:
        print("https:     failed - %s" % (probe["hint"] or probe["error"]))
    if report["ok"] and not probe["ok"]:
        print("next:      provision the site (Domains -> Provision), then run this again")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())




