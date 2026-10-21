"""The weekly brief: what changed, and what is worth changing because of it.

Deliberately small. The review job gets numbers, not a data dump: totals, deltas against
the previous period, top pages, error counts and - the part that makes advice useful -
the metric each recommendation has to cite.

Only aggregates are ever read here, so nothing personal can reach a prompt.
"""

from datetime import datetime, timedelta, timezone

import analytics


def _day(offset_days):
    return (datetime.now(timezone.utc) - timedelta(days=offset_days)).strftime("%Y-%m-%d")


def _totals(rows, metric):
    return sum(row["value"] for row in rows if row["metric"] == metric)


def brief(org_id, days=7):
    """Two windows side by side, so every number has a comparison."""
    current_since, previous_since = _day(days), _day(days * 2)
    rows = analytics.metrics(org_id, previous_since, limit=20000)
    current = [r for r in rows if r["day"] >= current_since]
    previous = [r for r in rows if r["day"] < current_since]

    def block(window):
        pages = {}
        for row in window:
            if row["metric"] == "page":
                pages[row["dimension"]] = pages.get(row["dimension"], 0) + row["value"]
        return {
            "pageviews": int(_totals(window, "pageviews")),
            "sessions": int(_totals(window, "sessions")),
            "errors": int(_totals(window, "errors")),
            "top_pages": [
                {"path": path, "views": int(views)}
                for path, views in sorted(pages.items(), key=lambda p: p[1], reverse=True)[:5]
            ],
        }

    now_block = block(current)
    before_block = block(previous)
    return {
        "since": current_since,
        "days": days,
        "sources": sorted({row["source"] for row in rows}),
        "current": now_block,
        "previous": before_block,
        "change": {
            "pageviews": now_block["pageviews"] - before_block["pageviews"],
            "sessions": now_block["sessions"] - before_block["sessions"],
            "errors": now_block["errors"] - before_block["errors"],
        },
    }


def prompt_for(brief_data, domain):
    """The task text for the review job. Short, and it demands evidence."""
    block = brief_data["current"]
    change = brief_data["change"]
    top = ", ".join("%s (%d)" % (page["path"], page["views"])
                    for page in block["top_pages"]) or "no page data"
    return (
        "Weekly review for %s.\n"
        "Last %d days: %d pageviews (%+d vs previous), %d sessions (%+d), "
        "%d errors (%+d).\nTop pages: %s.\n\n"
        "Propose at most three concrete changes to the site. Each one must name the "
        "metric that justifies it. Do not rewrite the whole page, do not invent traffic "
        "figures, and if the data is too thin to justify a change, say so and propose "
        "nothing. Save the proposals with save_deliverable using kind \"proposal\", "
        "not \"website\" - nothing you write here is published automatically."
        % (
            domain,
            brief_data["days"],
            block["pageviews"], change["pageviews"],
            block["sessions"], change["sessions"],
            block["errors"], change["errors"],
            top,
        )
    )


def digest(brief_data, domain, proposals=None):
    """``(subject, html)`` for the weekly note. Plain, factual, one screen long."""
    block = brief_data["current"]
    change = brief_data["change"]

    def delta(value):
        return "%+d" % value

    rows = "".join(
        "<tr><td style='padding:4px 12px 4px 0'>%s</td><td>%s</td>"
        "<td style='color:#6b7280'>%s</td></tr>" % (label, value, delta(value2))
        for label, value, value2 in (
            ("Pageviews", block["pageviews"], change["pageviews"]),
            ("Sessions", block["sessions"], change["sessions"]),
            ("Errors", block["errors"], change["errors"]),
        )
    )
    items = "".join(
        "<li><strong>%s</strong> - %s</li>" % (p.get("title", "Untitled"), p.get("summary", ""))
        for p in (proposals or [])
    ) or "<li>No changes worth proposing this week.</li>"
    html = (
        "<div style='font-family:system-ui,sans-serif;color:#111827;max-width:560px'>"
        "<h2 style='margin:0 0 4px'>Weekly review: %s</h2>"
        "<p style='color:#6b7280;margin:0 0 16px'>Last %d days, against the %d before.</p>"
        "<table style='border-collapse:collapse;font-size:14px'>%s</table>"
        "<h3 style='margin:20px 0 8px;font-size:15px'>Proposals</h3>"
        "<ul style='padding-left:18px;font-size:14px;line-height:1.6'>%s</ul>"
        "<p style='color:#6b7280;font-size:13px;margin-top:20px'>Nothing has been "
        "published. Open the Library to read, edit or discard each proposal.</p></div>"
        % (domain, brief_data["days"], brief_data["days"], rows, items)
    )
    return "Weekly review: %s" % domain, html


async def site_review(job, emit):
    """Run one weekly review for a job. Registered in jobs.py as task ``site_review``."""
    import agents
    import sites as sites_service

    org_id = job.get("org_id")
    site = sites_service.get_site(job["site_id"]) if job.get("site_id") else None
    if not site and org_id is not None:
        # Jobs do not carry a site id, and most clients have exactly one site, so the
        # org's first site is the review target.
        candidates = sites_service.list_sites(org_id)
        site = candidates[0] if candidates else None
    if not site:
        emit("system", "error", "This review has no site to look at.")
        return "failed: no site"
    data = brief(org_id, days=int(job.get("days") or 7))
    if not data["current"]["pageviews"] and not data["current"]["sessions"]:
        # A review with no data would invent advice, which is worse than none.
        emit("system", "system",
             "No metrics for the last %d days - nothing to review yet." % data["days"])
        return "finished: no data"
    task = dict(job)
    task["prompt"] = prompt_for(data, site["domain"])
    result = await agents.run_team(task, emit)

    # The proposals are the product; the email is only the notification, so a mail
    # failure is reported and never fails an otherwise successful review.
    try:
        import deliverables as deliverables_service
        import platform_mail

        proposals = [
            item for item in deliverables_service.list_deliverables("proposal", org_id)
            if item.get("job_id") == job.get("id")
        ]
        recipient = (site.get("contact_email") or "").strip()
        if recipient:
            subject, html = digest(data, site["domain"], proposals)
            platform_mail.send(recipient, subject, html)
            emit("system", "system", "Digest sent to %s." % recipient)
        else:
            emit("system", "system", "No digest sent: the site has no contact email.")
    except Exception as exc:
        emit("system", "system", "Review finished, digest not sent: %s" % exc)
    return result

