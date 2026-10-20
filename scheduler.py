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
        except Exception as exc:  # pragma: no cover - defensive
            print("[scheduler] sync cycle crashed: %s: %s" % (type(exc).__name__, exc))
        time.sleep(hours * 3600)


def start():
    """Begin the recurring sync if one is configured. Idempotent per process."""
    global _started
    hours = sync_interval_hours()
    if _started or hours <= 0:
        return False
    _started = True
    thread = threading.Thread(target=_loop, args=(hours,), name="site-log-sync", daemon=True)
    thread.start()
    return True
