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


async def site_review(job, emit):
    """Run one weekly review for a job. Registered in jobs.py as task ``site_review``."""
    import agents
    import sites as sites_service

    org_id = job.get("org_id")
    site = sites_service.get_site(job["site_id"]) if job.get("site_id") else None
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
    return await agents.run_team(task, emit)

