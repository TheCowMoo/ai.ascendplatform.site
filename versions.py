"""An undo for published pages.

The review job changes live sites on a schedule, so every publish must leave the
previous content behind and restoring must be one call - otherwise the first bad
autonomous edit is unrecoverable and only the client notices.
"""

from db import get_connection, now_str


def record(site_id, content_html, source="", deliverable_id=None, org_id=None):
    """Store a version. Cheap enough to do on every write."""
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO site_versions (site_id, org_id, content_html, source, "
            "deliverable_id) VALUES (?, ?, ?, ?, ?)",
            (site_id, org_id, content_html or "", source, deliverable_id),
        )
        return cur.lastrowid


def versions(site_id, limit=20):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, site_id, org_id, source, deliverable_id, created_at, "
            "length(content_html) AS bytes FROM site_versions WHERE site_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (site_id, max(1, int(limit))),
        ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def get(version_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM site_versions WHERE id = ?", (version_id,)
        ).fetchone()
    return {key: row[key] for key in row.keys()} if row else None


def restore(version_id):
    """Put a stored version back live, recording the swap so it is itself undoable."""
    import sites as sites_service

    version = get(version_id)
    if not version:
        raise ValueError("No such version")
    site = sites_service.get_site(version["site_id"])
    if not site:
        raise ValueError("The site this version belongs to no longer exists")
    record(site["id"], site.get("content_html") or "", source="before-restore",
           org_id=site.get("org_id"))
    sites_service.update_site(site["id"], {"content_html": version["content_html"]})
    return sites_service.get_site(site["id"])
