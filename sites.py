"""
Site records (stage S5).

The row is the desired state; ``provisioner`` makes the target match it. This
module is the thin CRUD/status layer used by both the API and the agent tools.
"""

import json
import secrets

import actions
import jobs
from db import days_ago, get_connection, now_str

TEXT_FIELDS = (
    "domain", "docroot", "content_html", "linked_bot_id", "target",
    "status", "ssl_status", "url", "last_error", "dns_last_ip", "dns_last_check",
)

STATUSES = ("pending_dns", "dns_ok", "live", "error")


def _as_dict(row):
    return {k: row[k] for k in row.keys()}


def site_out(row):
    """Serialise a site row, parsing the aliases JSON."""
    out = _as_dict(row)
    try:
        out["aliases"] = json.loads(row["aliases"] or "[]")
    except Exception:
        out["aliases"] = []
    out["url"] = out["url"] or ""
    return out


def list_sites(org_id=None, limit=100):
    """Sites for one client, or every site when ``org_id`` is omitted.

    ``org_id=None`` is the platform operator's view. A client must always pass its
    own org - this parameter used to be ignored by the route, which is why every
    logged-in user could list every client's domains. Note the ``is not None``: 0 is
    the "no access" marker and must filter, not fall through to "no filter".
    """
    query = "SELECT * FROM sites"
    params = []
    if org_id is not None:
        query += " WHERE org_id = ?"
        params.append(org_id)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [site_out(r) for r in rows]


def get_site(site_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    return site_out(row) if row else None


def get_site_by_domain(domain, org_id=None):
    """Look a site up by domain.

    Domains are globally unique, so the unscoped form answers "does anyone own this
    domain yet" - which is what creating one needs. Pass ``org_id`` for a scoped
    lookup, which is what a client request must use.
    """
    value = (domain or "").strip().lower()
    if not value:
        return None
    query = "SELECT * FROM sites WHERE domain = ?"
    params = [value]
    if org_id is not None:
        query += " AND org_id = ?"
        params.append(org_id)
    with get_connection() as conn:
        row = conn.execute(query, params).fetchone()
    return site_out(row) if row else None


def create_site(data, owner_id=None, org_id=None):
    """Create a site. ``owner_id`` is the creator, ``org_id`` is the tenant that owns it."""
    domain = (data.get("domain") or "").strip().lower()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO sites (owner_id, org_id, domain, aliases, docroot, content_html, linked_bot_id, target, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                owner_id if owner_id is not None else data.get("owner_id"),
                org_id if org_id is not None else data.get("org_id"),
                domain,
                json.dumps(data.get("aliases") or []),
                (data.get("docroot") or "").strip(),
                data.get("content_html") or "",
                (data.get("linked_bot_id") or "").strip(),
                (data.get("target") or "local").strip() or "local",
                "pending_dns",
            ),
        )
        site_id = cur.lastrowid
    return get_site(site_id)


def update_site(site_id, data):
    if not get_site(site_id):
        return None
    sets, values = [], []
    for field in TEXT_FIELDS:
        if field in data:
            value = data.get(field)
            sets.append("%s = ?" % field)
            values.append(value.strip() if isinstance(value, str) else (value or ""))
    if "aliases" in data:
        raw = data.get("aliases")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = []
        sets.append("aliases = ?")
        values.append(json.dumps(raw or []))
    if not sets:
        return get_site(site_id)
    sets.append("updated_at = ?")
    values.append(now_str())
    values.append(site_id)
    with get_connection() as conn:
        conn.execute("UPDATE sites SET %s WHERE id = ?" % ", ".join(sets), values)
    return get_site(site_id)


def delete_site(site_id):
    with get_connection() as conn:
        conn.execute("DELETE FROM sites WHERE id = ?", (site_id,))
    return True


def upsert_for_agent(domain, owner_id=None, org_id=None):
    """Find the site for a domain, creating it if the agent asks for a new one."""
    existing = get_site_by_domain(domain)
    if existing:
        return existing
    return create_site(
        {"domain": domain, "target": "local", "aliases": []},
        owner_id=owner_id,
        org_id=org_id,
    )


def apply_result(site_id, result):
    """Persist the outcome of a provision run onto the site row."""
    dns = result.get("dns") or {}
    payload = {
        "status": result.get("status") or "error",
        "url": result.get("url") or "",
        "dns_last_ip": ", ".join(dns.get("resolved") or [])[:200],
        "dns_last_check": now_str(),
        "ssl_status": "active" if result.get("status") == "live" else "pending",
        "last_error": "" if result.get("ok") else (result.get("error") or _first_failure(result)),
    }
    return update_site(site_id, payload)


def _first_failure(result):
    for step in result.get("steps") or []:
        if not step.get("ok"):
            return "%s: %s" % (step.get("step"), (step.get("detail") or "")[:300])
    return ""


# How many automatic attempts one domain gets in a day. The DNS sweep runs hourly, so
# without a cap a domain whose port 80 is closed would ask Let's Encrypt for a
# certificate every hour - and Let's Encrypt answers that with a rate limit that
# outlasts the outage.
AUTO_PROVISION_WINDOW_HOURS = 24
AUTO_PROVISION_MAX_ATTEMPTS = 3


def _provision_jobs(site, limit=200):
    """Provisioning jobs for this domain, newest first, scoped to the site's tenant."""
    domain = (site.get("domain") or "").strip().lower()
    if not domain:
        return []
    rows = jobs.list_jobs(limit=limit, org_id=site.get("org_id"))
    return [row for row in rows
            if row.get("task") == "site_provision"
            and (row.get("prompt") or "").strip().lower() == domain]


def open_provision_job(site):
    """The queued or running provisioning job for this site, if there is one."""
    for row in _provision_jobs(site, limit=50):
        if row.get("status") in ("queued", "running"):
            return row
    return None


def queue_provision(site, requester_id=None, manual=False):
    """Queue one provisioning run for a site whose DNS now points here.

    This is the missing link in "point it here and SSL issues itself": the DNS sweep
    proves the name resolves here, then this hands the vhost and the certificate request
    to the job engine. Returns the job row, or ``None`` when nothing should be queued:

    * dry-run - a job would only simulate writes, and the Sites banner already says so;
    * a run is already queued or running (the hourly sweep and the Check button both
      notice the same domain, and two vhosts written at once is not a feature);
    * the automatic budget for the day is spent. Manual runs (``manual=True``) ignore the
      budget, so pressing Provision always does something.
    """
    if not site or not site.get("domain"):
        return None
    if not manual and actions.is_dry_run():
        return None
    if open_provision_job(site):
        return None
    recent = [row for row in _provision_jobs(site)
              if (row.get("created_at") or "") >= days_ago(AUTO_PROVISION_WINDOW_HOURS / 24.0)]
    if not manual and len(recent) >= AUTO_PROVISION_MAX_ATTEMPTS:
        note = ("automatic provisioning paused after %d attempts in %d hours - press "
                "Provision to retry" % (AUTO_PROVISION_MAX_ATTEMPTS,
                                        AUTO_PROVISION_WINDOW_HOURS))
        if site.get("last_error") != note:
            update_site(site["id"], {"last_error": note})
        return None
    domain = site["domain"]
    job_id = jobs.create_job(
        "Provision %s" % domain, domain,
        requester_id if requester_id is not None else site.get("owner_id"),
        "selector", "site_provision", org_id=site.get("org_id"),
    )
    jobs.submit(job_id)
    return jobs.get_job(job_id)


def new_site_token():
    return secrets.token_urlsafe(6)
