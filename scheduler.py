"""Recurring work: the daily sync that makes the analytics loop autonomous.

A daemon thread rather than a cron entry, so the platform stays a single deployable
image with no host-side crontab to forget. Two uvicorn workers each start one, which is
harmless because the sync is idempotent (metrics have a unique point key) - but it does
mean the work happens twice, so keep the interval sensible.

Off by default: ``SITE_LOG_SYNC_HOURS=0`` means nothing is scheduled, and a deployment
that has not configured Caddy does not log a failure every hour.
"""

import os
import threading
import time

_started = False


def sync_interval_hours():
    try:
        return max(0.0, float(os.environ.get("SITE_LOG_SYNC_HOURS") or 0))
    except ValueError:
        return 0.0


def review_weekday():
    """Weekday to run weekly reviews on, 0 = Monday. 7 or unset = off."""
    try:
        value = int(os.environ.get("SITE_REVIEW_WEEKDAY", "7"))
    except ValueError:
        return 7
    return value if 0 <= value <= 6 else 7


def due_today(now=None):
    """Is a weekly review due? Kept pure so the schedule can be tested without waiting."""
    import datetime as _dt

    wanted = review_weekday()
    if wanted > 6:
        return False
    now = now or _dt.datetime.now(_dt.timezone.utc)
    return now.weekday() == wanted
    try:
        return max(0.0, float(os.environ.get("SITE_LOG_SYNC_HOURS") or 0))
    except ValueError:
        return 0.0


def sync_all():
    """Pull every enabled source, one sync_runs row each, so failures are attributable."""
    import analytics

    results = []
    for source in analytics.sources(None):
        if not source["enabled"]:
            continue
        kind = source["kind"]
        try:
            if kind == "caddy":
                results.append(("caddy", site_logs_sync()))
            elif kind == "ga4":
                import ga4

                run = analytics.start_sync(source["id"])
                try:
                    summary = ga4.sync(source)
                except Exception as exc:
                    analytics.finish_sync(run, "error", 0, str(exc))
                    results.append((kind, {"ok": False, "error": str(exc)}))
                    continue
                analytics.finish_sync(run, "ok", summary.get("rows", 0))
                results.append((kind, summary))
            else:
                # ghl and meta adapters are not written yet; say so rather than
                # pretending a sync happened.
                results.append((kind, {"ok": False, "error": "no adapter for %r yet" % kind}))
        except Exception as exc:  # pragma: no cover - defensive
            results.append((kind, {"ok": False, "error": str(exc)}))
    return results


def site_logs_sync():
    import site_logs

    return site_logs.sync()


def _loop(hours):
    while True:
        try:
            for kind, result in sync_all():
                if not result.get("ok"):
                    print("[scheduler] %s sync: %s" % (kind, result.get("error", "unknown")))
            queued = queue_reviews()
            if queued:
                print("[scheduler] queued %d weekly review(s)" % queued)
        except Exception as exc:  # pragma: no cover - defensive
            print("[scheduler] sync cycle crashed: %s: %s" % (type(exc).__name__, exc))
        time.sleep(hours * 3600)


def queue_reviews(now=None):
    """Queue one review per client, guarded by the jobs table itself.

    The guard is a query rather than new state: if a ``site_review`` job already exists
    for that org in the last six days, this week has been done. That survives restarts,
    which a 'last run' variable in memory would not.
    """
    import datetime as _dt

    import jobs
    import sites
    from db import get_connection

    if not due_today(now):
        return 0
    since = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=6)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    queued = 0
    for site in sites.list_sites():
        org_id = site.get("org_id")
        if org_id is None:
            continue
        with get_connection() as conn:
            existing = conn.execute(
                "SELECT id FROM jobs WHERE task = ? AND org_id = ? AND created_at >= ?",
                ("site_review", org_id, since),
            ).fetchone()
        if existing:
            continue
        job_id = jobs.create_job(
            "Weekly review: %s" % site["domain"],
            "Weekly site review",
            None,
            "selector",
            "site_review",
            org_id=org_id,
        )
        # A created job that is never submitted stays queued forever - the same defect
        # this endpoint was written to avoid.
        jobs.submit(job_id)
        queued += 1
    return queued


def verify_interval_minutes():
    """How often to re-check domains waiting on DNS. 0 turns it off."""
    try:
        return max(0.0, float(os.environ.get("SITE_VERIFY_MINUTES", "60") or 0))
    except ValueError:
        return 60.0


def _stamp():
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def verify_pending(limit=20):
    """Re-check the domains that are waiting on DNS, and record what the web says.

    This is what makes "point it here and SSL issues itself" true without a human: a
    client who fixes a record at midnight finds the domain green in the morning. It is
    read-only - a DNS lookup and an HTTPS handshake - so it needs no approval gate, and
    the handshake is also what asks Caddy for a certificate on a site that has not been
    visited yet.
    """
    import provisioner
    import sites as sites_service

    checked = 0
    for site in sites_service.list_sites(limit=200):
        if checked >= limit:
            break
        if site.get("status") not in ("pending_dns", "dns_ok"):
            continue
        if site.get("org_id") is None:
            continue
        try:
            report = provisioner.dns_report(site["domain"])
            probe = provisioner.live_probe(site["domain"])
        except Exception:
            continue          # a resolver hiccup must not stop the sweep
        fields = {"dns_last_ip": ", ".join(report["resolved"])[:200],
                  "dns_last_check": _stamp()}
        if probe["ok"]:
            fields.update({"status": "live", "ssl_status": "verified",
                           "url": "https://" + site["domain"], "last_error": ""})
        else:
            fields["ssl_status"] = "failed" if probe["error"] else "pending"
            fields["last_error"] = (probe["hint"] or probe["error"])[:200]
            fields["status"] = "dns_ok" if report["ok"] else "pending_dns"
        try:
            sites_service.update_site(site["id"], fields)
            checked += 1
        except Exception:
            continue
    return checked


_verify_started = False


def start_verifier():
    """Hourly sweep in its own thread. Idempotent per process; 0 minutes disables it."""
    global _verify_started
    minutes = verify_interval_minutes()
    if _verify_started or minutes <= 0:
        return False
    _verify_started = True

    def _tick():
        while True:
            try:
                verify_pending()
            except Exception:
                pass
            time.sleep(minutes * 60)

    threading.Thread(target=_tick, name="site-verify", daemon=True).start()
    return True


def start():
    """Begin the recurring sync if one is configured. Idempotent per process."""
    global _started
    # Independent of the sync: the DNS sweep is useful in a deployment that has no site
    # logs configured at all, which is every fresh install.
    start_verifier()
    hours = sync_interval_hours()
    if _started or hours <= 0:
        return False
    _started = True
    thread = threading.Thread(target=_loop, args=(hours,), name="site-log-sync", daemon=True)
    thread.start()
    return True
