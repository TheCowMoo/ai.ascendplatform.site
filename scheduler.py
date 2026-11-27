"""Recurring work: the coordinator tick, the weekly review, and the log sync.

A daemon thread rather than a cron entry, so the platform stays a single deployable
image with no host-side crontab to forget. **Two uvicorn workers start one each**, so
every sweep runs under a PostgreSQL advisory lock: the holder does the work and the
other worker skips that tick. Without it, a deploy granted the monthly allowance
twice and queued two copies of every scheduled run.

``SITE_LOG_SYNC_HOURS`` (default 0) only controls the analytics/site-log sync; the
coordination tick is independent. It re-checks pending DNS, starts due automations,
wakes delayed runs, evaluates analytics alerts, queues weekly reviews and grants the
monthly allowance. ``SITE_VERIFY_MINUTES`` (default 60) is its interval; setting it
to 0 disables only the DNS check. ``SCHEDULER_ENABLED=0`` turns all background work
off for a test or a maintenance window.
"""

import os
import threading
import time

import db

_started = False

# Stable lock namespaces. Never derive these from a row id: two unrelated sweeps
# sharing a lock would silently stop one of them.
TICK_LOCK_KEY = 20261127
SYNC_LOCK_KEY = 20261128


def scheduler_enabled():
    """False only when the operator explicitly turns background work off."""
    raw = (os.environ.get("SCHEDULER_ENABLED") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


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
    """The opt-in analytics/site-log sync. Its own lock, its own interval."""
    while True:
        try:
            with db.advisory_lock(SYNC_LOCK_KEY) as owned:
                if owned:
                    for kind, result in sync_all():
                        if not result.get("ok"):
                            print("[scheduler] %s sync: %s"
                                  % (kind, result.get("error", "unknown")))
        except Exception as exc:  # pragma: no cover - defensive
            print("[scheduler] sync cycle crashed: %s: %s" % (type(exc).__name__, exc))
        time.sleep(hours * 3600)


def _run_tick_tasks(verify=True):
    """One coordination pass. Every task is isolated: one bad sweep must not skip the
    monthly grant or the next client's review."""
    if verify:
        try:
            verify_pending()
        except Exception:
            pass
    try:
        import automations

        automations.run_due()
        automations.wake_due()
    except Exception:
        pass
    try:
        queued = queue_alerts()
        if queued:
            print("[scheduler] queued %d analytics alert run(s)" % queued)
    except Exception:
        pass
    try:
        queued = queue_reviews()
        if queued:
            print("[scheduler] queued %d weekly review(s)" % queued)
    except Exception:
        pass
    try:
        housekeeping()
    except Exception:
        pass
    try:
        import usage

        # A voice session whose browser never came back is billed at its
        # reservation; the monthly grant is skipped if the sweep raises, and the
        # next tick tries again.
        usage.sweep_voice_sessions()
        usage.grant_monthly()
    except Exception:
        pass          # a grant failure must not stop the DNS sweep


def queue_alerts(now=None):
    """Queue at most one analytics-alert evaluation per client every 20 hours."""
    import datetime as _dt

    import analytics
    import analytics_alerts  # noqa: F401  (registers the task)
    import jobs
    import orgs
    from db import get_connection

    moment = now or _dt.datetime.now(_dt.timezone.utc)
    cutoff = (moment - _dt.timedelta(hours=20)).strftime("%Y-%m-%d %H:%M:%S")
    queued = 0
    for org in orgs.list_orgs(limit=500):
        if org.get("status") != "active":
            continue
        org_id = org.get("id")
        try:
            import settings
            if not settings.get_bool("ANALYTICS_ALERTS_ENABLED", True, org_id=org_id):
                continue
            sources = analytics.sources(org_id)
        except Exception:
            continue
        if not any(source.get("enabled") for source in sources):
            continue
        with get_connection() as conn:
            existing = conn.execute(
                "SELECT id FROM jobs WHERE task = ? AND org_id = ? AND created_at >= ?",
                ("analytics_alerts", org_id, cutoff),
            ).fetchone()
        if existing:
            continue
        try:
            job_id = jobs.create_job(
                "Analytics alerts: %s" % (org.get("name") or "client"),
                "Evaluate analytics alert rules",
                None,
                "selector",
                "analytics_alerts",
                org_id=org_id,
            )
            jobs.submit(job_id)
        except Exception:
            # One broken client must not stop the queue for everyone else.
            continue
        queued += 1
    return queued


_last_housekeeping = ""


def housekeeping(now=None):
    """Once a day, drop old audit/alert history. Cheap and safe to call hourly."""
    global _last_housekeeping
    import datetime as _dt

    moment = now or _dt.datetime.now(_dt.timezone.utc)
    stamp = moment.strftime("%Y-%m-%d")
    if _last_housekeeping == stamp:
        return {"skipped": True, "actions": 0, "alerts": 0}
    _last_housekeeping = stamp
    import analytics
    import analytics_alerts

    actions_removed = 0
    alerts_removed = 0
    try:
        actions_removed = analytics.prune_actions(180)
    except Exception:
        pass
    try:
        alerts_removed = analytics_alerts.prune_alerts(180)
    except Exception:
        pass
    return {"skipped": False, "actions": actions_removed, "alerts": alerts_removed}


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
        try:
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
            # A created job that is never submitted stays queued forever - the same
            # defect this endpoint was written to avoid.
            jobs.submit(job_id)
            queued += 1
        except Exception:
            # One broken client must not stop the queue for everyone else.
            continue
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
    client who fixes a record at midnight finds the domain green in the morning. The
    lookup and handshake are read-only, so they need no approval; the one write it causes
    is a *job*, and that job asks for a single approval before it touches the server.
    The handshake is also what asks Caddy for a certificate on a site that has not been
    visited yet.
    """
    import provisioner
    import sites as sites_service

    checked = 0
    for site in sites_service.list_sites(limit=200):
        if checked >= limit:
            break
        # "error" is what a failed provisioning run leaves behind. Leaving it out meant a
        # deployment that was since fixed never retried: the row stayed red until someone
        # pressed a button, which is the opposite of what this sweep promises.
        if site.get("status") not in ("pending_dns", "dns_ok", "error"):
            continue
        # An unowned site (created before POST /api/sites fell back to the acting client)
        # is exactly the one no client can refresh, so skipping it here is how a correct
        # DNS record stays "not detected" forever. This sweep is read-only; include it.
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
            updated = sites_service.update_site(site["id"], fields)
            # The name resolves here but does not answer HTTPS: that is the gap
            # provisioning fills. Queueing is deduplicated and capped inside the helper,
            # so an hourly sweep cannot become an hourly certificate request.
            if report["ok"] and not probe["ok"]:
                sites_service.queue_provision(updated or site)
            checked += 1
        except Exception:
            continue
    return checked


_verify_started = False


def start_verifier():
    """Start the coordination tick. Idempotent per process; the advisory lock makes it
    idempotent *across* processes too."""
    global _verify_started
    if _verify_started or not scheduler_enabled():
        return False
    _verify_started = True
    # 0 disables the DNS check, not the rest of the coordination. A deployment that
    # does not use managed DNS still needs due automations, alerts, reviews and grants.
    minutes = verify_interval_minutes()
    interval = minutes if minutes > 0 else 60

    def _tick():
        while True:
            try:
                with db.advisory_lock(TICK_LOCK_KEY) as owned:
                    if owned:
                        _run_tick_tasks(verify=minutes > 0)
            except Exception as exc:  # pragma: no cover - defensive
                print("[scheduler] tick crashed: %s: %s" % (type(exc).__name__, exc))
            time.sleep(interval * 60)

    threading.Thread(target=_tick, name="site-verify", daemon=True).start()
    return True


def start():
    """Begin the recurring work. Idempotent per process."""
    global _started
    if not scheduler_enabled():
        return False
    # Independent of the sync: the coordination tick is useful in a deployment that
    # has no site logs configured at all, which is every fresh install.
    start_verifier()
    hours = sync_interval_hours()
    if _started or hours <= 0:
        return False
    _started = True
    thread = threading.Thread(target=_loop, args=(hours,), name="site-log-sync", daemon=True)
    thread.start()
    return True
