"""An undo for published pages.

The review job changes live sites on a schedule, so every publish must leave the
previous content behind and restoring must be one call - otherwise the first bad
autonomous edit is unrecoverable and only the client notices.
"""

from db import get_connection, now_str


def record(site_id, content_html, source="", deliverable_id=None, org_id=None,
           path="index.html"):
    """Store a version. Cheap enough to do on every write.

    ``path`` is the file the content came from. The first implementation always stored
    the site's home-page content, so a subpage publish recorded the wrong page and
    restoring it did not undo the subpage at all.
    """
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO site_versions (site_id, org_id, content_html, source, "
            "deliverable_id, path) VALUES (?, ?, ?, ?, ?, ?)",
            (site_id, org_id, content_html or "", source, deliverable_id,
             path or "index.html"),
        )
        return cur.lastrowid


def versions(site_id, limit=20):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, site_id, org_id, source, deliverable_id, path, created_at, "
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


def restore(version_id, actor=""):
    """Put a stored version back on disk, recording the swap so it is itself undoable.

    The previous version row held content but no path, so this only updated
    ``sites.content_html`` and the live file kept whatever the bad publish wrote. It now
    writes the file the snapshot came from, through the same provisioner target and
    approval row as a publish.
    """
    import actions
    import provisioner
    import sites as sites_service

    version = get(version_id)
    if not version:
        raise ValueError("No such version")
    site = sites_service.get_site(version["site_id"])
    if not site:
        raise ValueError("The site this version belongs to no longer exists")
    path = version.get("path") or "index.html"
    target, docroot = provisioner.target_docroot(site)
    file_path = provisioner.join_path(docroot, path)
    current = target.read_file(file_path)
    record(site["id"], current, source="before-restore", org_id=site.get("org_id"),
           path=path)

    summary = "restore %s on %s" % (path, site["domain"])
    live = not actions.is_dry_run()
    approval_id = None
    if live:
        approval_id = actions.request_approval(
            "file_write", summary,
            {"path": file_path, "bytes": len(version.get("content_html") or "")}, None)
        decided = actions.decide_approval(approval_id, "approved", actor or "operator")
        if not decided or decided.get("status") != "approved":
            raise ValueError("The restore could not be recorded as approved.")
    result = target.write_file(file_path, version.get("content_html") or "",
                               None, None, summary=summary, preapproved=live)
    if approval_id:
        actions.set_result(approval_id, result.get("output")
                           or ("wrote to %s" % file_path if result.get("ok")
                               else "the restore failed"))
    if not result.get("ok"):
        raise ValueError(result.get("output") or "The restore write failed.")
    if result.get("dry_run"):
        return site
    if path == "index.html":
        sites_service.update_site(site["id"],
                                  {"content_html": version.get("content_html") or ""})
    return sites_service.get_site(site["id"])
