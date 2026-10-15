"""
Agent tools for hosting websites (stage S5).

These are what let the Operator agent actually publish: it decides the domain,
and the tool writes the content, installs the web-server config, reloads it and
reports the URL. Every write/exec passes through ``tools.policy`` and
``actions.gate``, so in live mode nothing happens without a human approving it —
and in dry-run nothing happens at all.
"""

import json

import jobs
import provisioner
import sites as sites_service
from db import get_connection
from tools import register


def _requester(job_id):
    if not job_id:
        return None
    with get_connection() as conn:
        row = conn.execute("SELECT requester_id FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return row["requester_id"] if row else None


def _profile_for(owner_id):
    try:
        import profile as profile_mod

        return profile_mod.get_profile(owner_id) if owner_id else None
    except Exception:
        return None


def _summarise(result):
    lines = [
        "ok=%s status=%s target=%s"
        % (result.get("ok"), result.get("status"), result.get("target"))
    ]
    if result.get("url"):
        lines.append("url=%s" % result["url"])
    for step in result.get("steps") or []:
        lines.append("- %s: %s" % (step["step"], (step.get("detail") or "")[:200]))
    dns = result.get("dns") or {}
    if dns:
        lines.append("dns resolved=%s expected=%s" % (dns.get("resolved"), dns.get("expected")))
    return "\n".join(lines)


def _build_provision_site(emit, job_id=None):
    async def provision_site(domain: str) -> str:
        """Create or update the website for a domain and return its URL."""
        owner_id = _requester(job_id)
        site = sites_service.upsert_for_agent(domain, owner_id)
        if not site:
            return "Could not create a site record for %r." % domain
        profile = _profile_for(owner_id or site.get("owner_id"))
        try:
            result = provisioner.provision(site, profile=profile, job_id=job_id, emit=emit)
        except provisioner.ProvisionError as exc:
            sites_service.update_site(site["id"], {"status": "error", "last_error": str(exc)})
            return "Provisioning refused: %s" % exc
        sites_service.apply_result(site["id"], result)
        return _summarise(result)

    return provision_site


def _build_check_site_dns(emit, job_id=None):
    async def check_site_dns(domain: str) -> str:
        """Check whether a domain's DNS already points at this server."""
        info = provisioner.check_dns(domain)
        return json.dumps(
            {
                "domain": domain,
                "ok": info["ok"],
                "resolved": info["resolved"],
                "expected": info["expected"],
                "reason": info["reason"],
                "records_to_create": provisioner.dns_records(domain),
            },
            indent=1,
        )

    return check_site_dns


def _build_write_site_content(emit, job_id=None):
    async def write_site_content(domain: str, html: str) -> str:
        """Replace a site's index.html with the given HTML (goes through the approval gate)."""
        site = sites_service.get_site_by_domain(domain)
        if not site:
            return "No site record for %r. Create it first with provision_site." % domain
        target, docroot = provisioner.target_docroot(site)
        path = provisioner.join_path(docroot, "index.html")
        result = target.write_file(path, html, job_id, emit,
                                   summary="update index.html for %s" % domain)
        if result.get("ok") and not result.get("dry_run"):
            sites_service.update_site(site["id"], {"content_html": html})
        return "ok=%s %s" % (result.get("ok"), result.get("output"))

    return write_site_content


def _build_list_sites(emit, job_id=None):
    async def list_sites() -> str:
        """List the websites this platform manages, with their URL and status."""
        rows = sites_service.list_sites()
        if not rows:
            return "No sites yet."
        return json.dumps(
            [
                {
                    "domain": row["domain"],
                    "status": row["status"],
                    "target": row["target"],
                    "url": row["url"] or ("https://" + row["domain"]),
                }
                for row in rows
            ],
            indent=1,
        )

    return list_sites


register(
    "provision_site",
    "Create or update the website for a domain on the configured target and return its URL.",
    _build_provision_site,
)
register(
    "check_site_dns",
    "Check whether a domain's DNS already points at this server, and list the records to create.",
    _build_check_site_dns,
)
register(
    "write_site_content",
    "Replace a site's index.html with the supplied HTML.",
    _build_write_site_content,
)
register(
    "list_sites",
    "List the websites this platform manages, with URL and status.",
    _build_list_sites,
)


# ---------------------------------------------------------------------------
# Job task
# ---------------------------------------------------------------------------
def _run_provision(site, job_id, emit):
    """Shared by the agent tool and the job task."""
    owner_id = site.get("owner_id")
    profile = _profile_for(owner_id)
    try:
        result = provisioner.provision(site, profile=profile, job_id=job_id, emit=emit)
    except provisioner.ProvisionError as exc:
        sites_service.update_site(site["id"], {"status": "error", "last_error": str(exc)})
        return "provisioning refused: %s" % exc
    sites_service.apply_result(site["id"], result)
    return _summarise(result)


@jobs.task("site_provision")
async def site_provision_task(job, emit):
    """Job task: provision the site whose domain is the job prompt.

    Runs on the job engine (and therefore on the arq worker when configured), so
    provisioning is durable and its transcript - including any approval requests -
    streams to the UI.
    """
    domain = (job.get("prompt") or "").strip()
    if not domain:
        return "no domain supplied"
    site = sites_service.upsert_for_agent(domain, job.get("requester_id"))
    if not site:
        return "could not create a site record for %r" % domain
    return _run_provision(site, job.get("id"), emit)

