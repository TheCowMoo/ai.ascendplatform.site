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


def dns_records(domain, server_ip=None):
    """The records the user must create at their DNS provider."""
    ip = (server_ip or SERVER_IP) or "<this server's public IP>"
    return [
        {"type": "A", "name": domain, "value": ip, "ttl": 3600},
        {"type": "A", "name": "www." + domain, "value": ip, "ttl": 3600},
    ]


def check_dns(domain, server_ip=None):
    """Compare the published A records with this server's address."""
    ip = (server_ip or SERVER_IP).strip()
    addresses = resolve_domain(domain)
    ok = bool(ip) and ip in addresses
    return {
        "domain": domain,
        "resolved": addresses,
        "expected": ip or None,
        "ok": ok,
        "reason": "" if ok else ("no records published yet" if not addresses else "points elsewhere"),
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
<style>
  :root {{ --brand: {color}; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font-family: {font}; color: #1f2937; background: #f8fafc; }}
  header {{ background: var(--brand); color: #fff; padding: 72px 24px; text-align: center; }}
  header h1 {{ margin: 0 0 8px; font-size: 34px; }}
  header p {{ margin: 0; opacity: .9; }}
  main {{ max-width: 760px; margin: 0 auto; padding: 40px 24px; line-height: 1.6; }}
  h2 {{ color: var(--brand); }}
  footer {{ text-align: center; color: #6b7280; font-size: 13px; padding: 32px 24px; }}
</style>
</head>
<body>
<header>
  <h1>{title}</h1>
  <p>{tagline}</p>
</header>
<main>
{body}
</main>
<footer>Built with CMBGrowthAI</footer>
</body>
</html>
"""


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
    """A small, self-contained landing page used when no content is supplied."""
    return INDEX_TEMPLATE.format(
        title=html_escape(title),
        tagline=html_escape(tagline),
        body=body_html or "<p>This site is ready. Add content in the Sites tab.</p>",
        color=brand_color or "#4f46e5",
        font=font_family or "system-ui, sans-serif",
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




