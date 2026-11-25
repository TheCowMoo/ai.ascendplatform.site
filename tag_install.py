"""Install a Google Tag Manager container snippet into published site pages.

The snippet is injected at publish time and can be applied to already-published pages by
the Tag Manager install route. It is idempotent: a page that already contains the
container marker is left alone, so a republish cannot duplicate the tag.
"""

import re

MARKER = "<!-- GTM:%s -->"


def snippet(public_id):
    """The standard GTM head/body snippets for one container."""
    ident = str(public_id or "").strip()
    if not ident or not re.fullmatch(r"GTM-[A-Za-z0-9]+", ident):
        return "", ""
    head = (
        "\n<!-- GTM:%s -->\n"
        "<script>(function(w,d,s,l,i){w[l]=w[l]||[];w[l].push({'gtm.start':"
        "new Date().getTime(),event:'gtm.js'});var f=d.getElementsByTagName(s)[0],"
        "j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src="
        "'https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);"
        "})(window,document,'script','dataLayer','%s');</script>\n" % (ident, ident)
    )
    body = (
        "\n<noscript><iframe src=\"https://www.googletagmanager.com/ns.html?id=%s\" "
        "height=\"0\" width=\"0\" style=\"display:none;visibility:hidden\"></iframe>"
        "</noscript>\n" % ident
    )
    return head, body


def inject(html, public_id):
    """Insert the container once. Returns the page unchanged when it is already there."""
    text = html or ""
    ident = str(public_id or "").strip()
    if not text.strip() or not ident or not re.fullmatch(r"GTM-[A-Za-z0-9]+", ident):
        return text
    if ident in text:
        return text
    head, body = snippet(ident)
    if not head:
        return text
    lowered = text.lower()
    if "</head>" in lowered:
        index = lowered.index("</head>")
        text = text[:index] + head + text[index:]
    else:
        text = head + text
    lowered = text.lower()
    if "</body>" in lowered:
        index = lowered.index("</body>")
        # The body snippet belongs immediately after <body>, not at the end.
        body_open = lowered.find("<body")
        if body_open != -1:
            close = lowered.find(">", body_open)
            if close != -1:
                text = text[:close + 1] + body + text[close + 1:]
            else:
                text = text[:index] + body + text[index:]
        else:
            text = text[:index] + body + text[index:]
    else:
        text = text + body
    return text


def container_for_org(org_id):
    """The first GTM web container a client has selected, or None."""
    import analytics

    for row in analytics.containers(org_id):
        if (row.get("public_id") or "").strip():
            return row
    return None


def install_for_site(site, public_id):
    """Inject the snippet into the site's live pages.

    The files are the source of truth for what the visitor sees, so the page is read
    back and rewritten rather than republishing a possibly older draft. The caller is an
    explicit client-admin action; it records an analytics action for the audit trail.
    """
    import deliverables as deliverables_service
    import provisioner

    ident = str(public_id or "").strip()
    if not site or not ident:
        return {"ok": False, "error": "No Tag Manager container is connected.",
                "files": 0, "updated": 0, "errors": []}
    target, docroot = provisioner.target_docroot(site)
    errors = []
    files = 0
    updated = 0
    dry_run = False

    def apply(relative, current):
        nonlocal files, updated, dry_run
        if not (current or "").strip():
            return
        files += 1
        changed = inject(current, ident)
        if changed == current:
            return
        path = provisioner.join_path(docroot, relative)
        try:
            import versions as versions_service

            versions_service.record(
                site["id"], current, source="analytics_tag",
                org_id=site.get("org_id"), path=relative)
        except Exception:
            # A snapshot is valuable but must not stop the tag installation itself.
            pass
        result = target.write_file(path, changed, None, None,
                                   summary="install GTM container %s on %s"
                                           % (ident, site.get("domain") or relative),
                                   preapproved=True)
        if result.get("dry_run"):
            dry_run = True
        if result.get("ok"):
            updated += 1
        else:
            errors.append(result.get("output") or "write failed")

    home = site.get("content_html") or ""
    if home.strip():
        index_path = provisioner.join_path(docroot, "index.html")
        # The live file is what the visitor sees; site.content_html is the fallback when
        # the site has never been provisioned. Reading the live file avoids overwriting
        # an edit made after the site row was last updated.
        home_current = target.read_file(index_path) or home
        apply("index.html", home_current)
        tagged_home = inject(home_current, ident)
        if tagged_home != home:
            try:
                import sites as sites_service

                sites_service.update_site(site["id"], {"content_html": tagged_home})
            except Exception:
                pass

    rows = []
    for kind in ("website", "blog"):
        try:
            rows.extend(deliverables_service.list_deliverables(kind, site.get("org_id")))
        except Exception:
            continue
    for item in rows:
        if item.get("site_id") != site.get("id") or item.get("status") != "published":
            continue
        relative = ""
        url = str(item.get("url") or "")
        if url:
            relative = re.sub(r"^https?://[^/]+/?", "", url)
        if not relative:
            relative = deliverables_service.page_path(item.get("slug"))
        if relative.startswith("/"):
            relative = relative.lstrip("/")
        if not relative:
            continue
        if relative == "index.html" and home.strip():
            continue
        apply(relative, target.read_file(provisioner.join_path(docroot, relative)))

    return {"ok": not errors, "files": files, "updated": updated,
            "errors": errors[:10], "container": ident, "dry_run": dry_run}
