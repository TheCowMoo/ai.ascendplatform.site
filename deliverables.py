"""
The content a client publishes (the Websites, Blogs, Funnels, Emails and Library
tabs).

A **deliverable** is the thing that goes live: a blog post, a funnel step, an email
campaign. It is not an artifact (a file a run emitted) and not a site (the place it
lands), which is why it has its own table and its own lifecycle:

    draft -> scheduled -> published        (or failed, from any of them)

``kind`` carries the tab it belongs to. The Library tab shows every kind, because
"everything the team has produced for you" is exactly the union of them.
"""

import re

from db import get_connection, now_str

KINDS = ("blog", "funnel", "email", "website", "asset")
STATUSES = ("draft", "scheduled", "published", "failed")

# Kind -> the console tab it appears in. Used by the save_deliverable tool's reply so
# the model can tell the user where to look.
TABS = {
    "blog": "Blogs",
    "funnel": "Funnels",
    "email": "Emails",
    "website": "Websites",
    "asset": "Library",
}

TEXT_FIELDS = ("title", "slug", "status", "summary", "content", "url")


class DeliverableError(Exception):
    """Raised for invalid input."""


def _as_dict(row):
    return {k: row[k] for k in row.keys()}


def slugify(value):
    text = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower())
    return text.strip("-")[:80]


def out(row):
    item = _as_dict(row)
    for key in ("summary", "content", "url", "slug"):
        item[key] = item.get(key) or ""
    return item


def list_deliverables(kind=None, org_id=None, limit=200):
    """Deliverables for one client, newest first.

    ``org_id=None`` is the platform operator's view and returns every client's.
    ``kind=None`` returns all of them - which is the Library tab.
    """
    query = "SELECT * FROM deliverables WHERE 1 = 1"
    params = []
    if org_id is not None:
        query += " AND org_id = ?"
        params.append(org_id)
    if kind:
        query += " AND kind = ?"
        params.append(kind)
    query += " ORDER BY updated_at DESC, id DESC LIMIT ?"
    params.append(max(1, int(limit)))
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [out(row) for row in rows]


def get_deliverable(deliverable_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM deliverables WHERE id = ?", (deliverable_id,)
        ).fetchone()
    return out(row) if row else None


def counts(org_id=None):
    """``{kind: count}`` plus ``all``, for the tab badges."""
    query = "SELECT kind, COUNT(*) AS n FROM deliverables WHERE 1 = 1"
    params = []
    if org_id is not None:
        query += " AND org_id = ?"
        params.append(org_id)
    query += " GROUP BY kind"
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    result = {kind: 0 for kind in KINDS}
    total = 0
    for row in rows:
        result[row["kind"]] = row["n"]
        total += row["n"]
    result["all"] = total
    return result


def _clean(data, partial=False):
    """Validate a payload. ``partial`` keeps absent fields absent, for a PATCH."""
    kind = (data.get("kind") or "blog").strip().lower()
    if kind not in KINDS:
        raise DeliverableError("kind must be one of: %s" % ", ".join(KINDS))
    status = (data.get("status") or "draft").strip().lower()
    if status not in STATUSES:
        raise DeliverableError("status must be one of: %s" % ", ".join(STATUSES))
    title = (data.get("title") or "").strip()
    if not partial and not title:
        raise DeliverableError("A title is required")
    return kind, status, title


def create_deliverable(data, org_id=None, created_by=None):
    data = data or {}
    kind, status, title = _clean(data)
    site_id = data.get("site_id")
    try:
        site_id = int(site_id) if site_id not in (None, "") else None
    except (TypeError, ValueError):
        site_id = None
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO deliverables (org_id, site_id, kind, title, slug, status, "
            "summary, content, url, job_id, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                org_id,
                site_id,
                kind,
                title,
                (data.get("slug") or slugify(title)),
                status,
                data.get("summary") or "",
                data.get("content") or "",
                (data.get("url") or "").strip(),
                data.get("job_id"),
                created_by,
            ),
        )
        deliverable_id = cur.lastrowid
    return get_deliverable(deliverable_id)


def update_deliverable(deliverable_id, data):
    """Patch a deliverable. Only the keys present are touched."""
    existing = get_deliverable(deliverable_id)
    if not existing:
        return None
    data = data or {}
    merged = dict(existing)
    merged.update({k: v for k, v in data.items() if k in TEXT_FIELDS or k in ("kind", "site_id")})
    kind, status, title = _clean(merged, partial=True)

    sets = ["kind = ?", "status = ?", "title = ?", "updated_at = ?"]
    values = [kind, status, title, now_str()]
    for field in ("slug", "summary", "content", "url"):
        if field in data:
            sets.append("%s = ?" % field)
            value = data.get(field)
            values.append(value.strip() if isinstance(value, str) else (value or ""))
    if "site_id" in data:
        sets.append("site_id = ?")
        try:
            values.append(int(data["site_id"]) if data["site_id"] not in (None, "") else None)
        except (TypeError, ValueError):
            values.append(None)
    values.append(deliverable_id)
    with get_connection() as conn:
        conn.execute(
            "UPDATE deliverables SET %s WHERE id = ?" % ", ".join(sets), values
        )
    return get_deliverable(deliverable_id)


def delete_deliverable(deliverable_id):
    with get_connection() as conn:
        conn.execute("DELETE FROM deliverables WHERE id = ?", (deliverable_id,))
    return True


# ---------------------------------------------------------------------------
# Publishing a page onto a provisioned site
# ---------------------------------------------------------------------------
def page_path(slug):
    """Where a page lives in a site's docroot.

    ``index.html`` for the home page, ``<slug>.html`` for everything else - so
    publishing a landing page never silently replaces the site's front page unless the
    slug says so.
    """
    cleaned = slugify(slug)
    if cleaned in ("", "home", "index"):
        return "index.html"
    return cleaned + ".html"


def _resolve_site(item, site_id):
    """The site to publish to, or DeliverableError explaining what to do about it."""
    import sites as sites_service

    if site_id in (None, ""):
        site_id = item.get("site_id")
    if site_id not in (None, ""):
        try:
            site = sites_service.get_site(int(site_id))
        except (TypeError, ValueError):
            raise DeliverableError("site_id must be a number")
        if not site:
            raise DeliverableError("No site with id %r." % (site_id,))
        if item.get("org_id") is not None and site.get("org_id") != item.get("org_id"):
            # A page belongs to the client whose console holds it; publishing it onto
            # another client's server would be a cross-tenant write.
            raise DeliverableError("That site belongs to another client.")
        return site

    candidates = sites_service.list_sites(item.get("org_id"))
    if not candidates:
        raise DeliverableError(
            "There is no site to publish to yet. Add a domain in the Websites tab first."
        )
    if len(candidates) > 1:
        raise DeliverableError(
            "This client has %d sites; pick one to publish to." % len(candidates)
        )
    return candidates[0]


def _slug_conflict(item, site):
    """Another published page already occupying the same file on the same site.

    Two drafts can share a slug while they are drafts; only publishing collides, and
    overwriting someone else's live page silently is exactly what this prevents.
    """
    wanted = page_path(item.get("slug"))
    for other in list_deliverables("website", item.get("org_id")):
        if other["id"] == item["id"] or other["status"] != "published":
            continue
        if page_path(other.get("slug")) != wanted:
            continue
        if other.get("site_id") in (None, site["id"]):
            return other
    return None


def publish_deliverable(deliverable_id, site_id=None, actor=""):
    """Write a ``website`` deliverable to a site's docroot and mark it published.

    Returns ``{ok, url, path, dry_run, output, deliverable}`` and raises
    ``DeliverableError`` for anything the operator can fix (wrong kind, no site, a
    slug already live).
    """
    import actions
    import provisioner

    item = get_deliverable(deliverable_id)
    if not item:
        raise DeliverableError("Deliverable not found")
    if item["kind"] != "website":
        raise DeliverableError(
            "Only website pages can be published to a site; %r has kind %r."
            % (item["title"], item["kind"])
        )
    if not (item.get("content") or "").strip():
        raise DeliverableError("This page has no content to publish yet.")

    site = _resolve_site(item, site_id)
    conflict = _slug_conflict(item, site)
    if conflict:
        raise DeliverableError(
            "%r is already published at that address on %s. Change the slug, or unpublish "
            "that page first." % (conflict["title"], site["domain"])
        )

    target, docroot = provisioner.target_docroot(site)
    path = provisioner.join_path(docroot, page_path(item.get("slug")))
    summary = "publish %r to %s" % (item["title"], site["domain"])

    # actions.gate() blocks until an operator decides, which would hang this request
    # until the approval times out. The person clicking Publish *is* that operator, so
    # record the decision first and let the gate read it back as approved: the audit
    # row ends up identical to an agent-initiated write, with this user's name on it.
    if not actions.is_dry_run():
        approval_id = actions.request_approval(
            "file_write", summary, {"path": path, "bytes": len(item["content"])}, None
        )
        actions.decide_approval(approval_id, "approved", actor or "operator")

    result = target.write_file(path, item["content"], None, None, summary=summary)
    if not result.get("ok"):
        raise DeliverableError(result.get("output") or "The write failed.")

    if result.get("dry_run"):
        return {
            "ok": True,
            "dry_run": True,
            "path": path,
            "url": "",
            "output": result.get("output") or "",
            "deliverable": item,
        }

    relative = page_path(item.get("slug"))
    url = "https://%s/%s" % (site["domain"], "" if relative == "index.html" else relative)
    # Snapshot before the write lands: an autonomous edit needs an undo, and the row
    # records what the page said immediately before this publish.
    import versions as versions_mod

    versions_mod.record(
        site["id"], site.get("content_html") or "", source="publish",
        deliverable_id=item["id"], org_id=item.get("org_id"),
    )
    updated = update_deliverable(
        item["id"], {"status": "published", "url": url, "site_id": site["id"]}
    )
    if relative == "index.html":
        # Mirror what write_site_content does, so the site row and the page agree.
        import sites as sites_service

        sites_service.update_site(site["id"], {"content_html": item["content"]})
    return {
        "ok": True,
        "dry_run": False,
        "path": path,
        "url": url,
        "output": result.get("output") or "",
        "deliverable": updated,
    }
