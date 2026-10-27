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

import actions
from db import get_connection
from tools import policy
from tools import ssh

# In the compose stack these are shared volumes between the app and Caddy.
SITES_ROOT = (os.environ.get("SITE_ROOT") or "/srv/sites").rstrip("/")
CADDY_SITES_DIR = (os.environ.get("CADDY_SITES_DIR") or "/etc/caddy/sites").rstrip("/")
CADDYFILE = os.environ.get("CADDYFILE") or "/etc/caddy/Caddyfile"
CADDY_RELOAD_CMD = (os.environ.get("CADDY_RELOAD_CMD") or ("caddy reload --config " + CADDYFILE)).strip()
SERVER_IP = (os.environ.get("SERVER_IP") or "").strip()

STEPS = ("dns", "content", "vhost", "reload")


class ProvisionError(Exception):
    """Raised when a target refuses or fails an operation."""


def slug(domain):
    """Lower-cased domain, guaranteed safe to use in a path ('' if invalid)."""
    decision = policy.check_domain(domain)
    return domain.strip().lower() if decision.allowed else ""


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


def dns_report(domain, server_ip_value=None):
    """Per-record truth for one domain: what to publish, what DNS says, and the verdict.

    One boolean cannot tell you that the apex is right and ``www`` was never created, which
    is the mistake people actually make, so every record gets its own row.
    """
    expected, source = (server_ip_value, "given") if server_ip_value else server_ip_source()
    apex4, apex6 = resolve_types(domain)
    www4, www6 = resolve_types("www." + domain)

    rows = [
        _record_row("A", "@", expected, apex4, True),
        _record_row("A", "www", expected, www4, False),
        _record_row("AAAA", "@", expected, apex6, False),
        _record_row("AAAA", "www", expected, www6, False),
    ]
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
    """The records the user must create at their DNS provider."""
    ip = (server_ip_value or server_ip()) or "<this server's public IP>"
    return [
        {"type": "A", "name": "@", "host": domain, "value": ip, "ttl": 3600},
        {"type": "A", "name": "www", "host": "www." + domain, "value": ip, "ttl": 3600},
    ]


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
	root * {docroot}
	file_server
}}
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


def render_vhost(domain, docroot, aliases=None):
    """Caddy site block for a domain plus its aliases."""
    hosts = [slug(domain)] + [slug(a) for a in (aliases or []) if a]
    hosts = [host for host in hosts if host]
    if not hosts:
        raise ProvisionError("no valid host for the site block")
    return VHOST_TEMPLATE.format(domain=slug(domain), hosts=", ".join(hosts), docroot=docroot)


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


def _authorise(tool, summary, args, job_id, emit, command=None, allow_mutating=True):
    """Policy first, then the approval gate. Shared by both targets."""
    if command is not None:
        decision = policy.check_command(command, allow_mutating=allow_mutating)
        if not decision.allowed:
            if emit:
                emit("policy", "error", "Blocked by policy: %s (%s)" % (command, decision.reason))
            return {"allowed": False, "blocked": True, "dry_run": actions.is_dry_run(),
                    "reason": decision.reason}
    return actions.gate(tool, summary, args, job_id, emit)


class LocalTarget:
    """The machine the app itself runs on."""

    name = "local"
    is_remote = False

    def __init__(self, sites_root=None, caddy_sites_dir=None):
        self.sites_root = (sites_root or SITES_ROOT).rstrip("/")
        self.caddy_sites_dir = (caddy_sites_dir or CADDY_SITES_DIR).rstrip("/")
        self.allowed_root = self.sites_root

    def run(self, command, job_id=None, emit=None, allow_mutating=True, summary=None):
        authorisation = _authorise("shell", summary or command, {"command": command},
                                   job_id, emit, command=command, allow_mutating=allow_mutating)
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

    def write_file(self, path, content, job_id=None, emit=None, summary=None):
        decision = check_write_path(self, path)
        if not decision.allowed:
            if emit:
                emit("policy", "error", "Refused to write %s: %s" % (path, decision.reason))
            return {"ok": False, "blocked": True, "dry_run": actions.is_dry_run(),
                    "output": "policy: %s" % decision.reason}
        authorisation = actions.gate("file_write", summary or ("write %s" % path),
                                     {"path": path, "bytes": len(content)}, job_id, emit)
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

    def run(self, command, job_id=None, emit=None, allow_mutating=True, summary=None):
        authorisation = _authorise(
            "ssh",
            summary or ("%s: %s" % (self.label(), command)),
            {"host": self.label(), "command": command},
            job_id, emit, command=command, allow_mutating=allow_mutating,
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

    def write_file(self, path, content, job_id=None, emit=None, summary=None):
        decision = check_write_path(self, path)
        if not decision.allowed:
            if emit:
                emit("policy", "error", "Refused to write %s: %s" % (path, decision.reason))
            return {"ok": False, "blocked": True, "dry_run": actions.is_dry_run(),
                    "output": "policy: %s" % decision.reason}

        authorisation = actions.gate(
            "ssh_write",
            summary or ("write %s on %s (%d bytes)" % (path, self.label(), len(content))),
            {"host": self.label(), "path": path, "bytes": len(content)},
            job_id, emit,
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
def provision(site, profile=None, job_id=None, emit=None, server_ip=None):
    """Make the target match the site row.

    Returns ``url``, ``status`` and per-step detail. In dry-run everything is
    simulated, so the returned URL is exactly what a live run would publish.
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

    dns = check_dns(domain, server_ip)
    record("dns", {"ok": dns["ok"]},
           "resolved=%s expected=%s %s" % (dns["resolved"], dns["expected"], dns["reason"]))

    content = (site.get("content_html") or "").strip() or index_from_profile(profile, site)
    record("content", target.write_file(join_path(docroot, "index.html"), content, job_id, emit,
                                        summary="write index.html for %s" % domain))

    vhost = render_vhost(domain, docroot, _aliases(site))
    record("vhost", target.write_file(join_path(target.caddy_sites_dir, domain + ".caddy"), vhost,
                                      job_id, emit,
                                      summary="install the web server config for %s" % domain))

    record("reload", target.run(CADDY_RELOAD_CMD, job_id, emit, allow_mutating=True,
                                summary="reload the web server (%s)" % target.name))

    # DNS is the user's job, so unpublished records are the expected state before
    # they create them — not a failure. `ok` reflects whether the *target* was
    # configured successfully; `status` distinguishes waiting-for-DNS from live.
    configured = all(step["ok"] for step in steps if step["step"] != "dns")
    status = "error" if not configured else ("live" if dns["ok"] else "pending_dns")
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


def remove_site(site, job_id=None, emit=None):
    """Remove the web-server config for a site (content is left in place)."""
    domain = slug((site or {}).get("domain"))
    if not domain:
        return {"ok": False, "error": "invalid or missing domain", "steps": []}
    target = get_target(site)
    steps = []
    vhost_path = join_path(target.caddy_sites_dir, domain + ".caddy")
    result = target.run("rm -f %s" % policy.quote(vhost_path), job_id, emit, allow_mutating=True,
                        summary="remove the web server config for %s" % domain)
    steps.append({"step": "remove_vhost", "ok": result["ok"], "detail": result.get("output", "")})
    reload_result = target.run(CADDY_RELOAD_CMD, job_id, emit, allow_mutating=True,
                               summary="reload the web server (%s)" % target.name)
    steps.append({"step": "reload", "ok": reload_result["ok"], "detail": reload_result.get("output", "")})
    return {"ok": all(step["ok"] for step in steps), "steps": steps}




