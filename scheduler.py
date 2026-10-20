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


def _loop(hours):
    import site_logs

    while True:
        try:
            result = site_logs.sync()
            if not result.get("ok"):
                # Log once per cycle rather than raising: a missing Caddy log must not
                # kill the thread and silently stop all future runs.
                print("[scheduler] site-log sync:", result.get("error", "unknown error"))
        except Exception as exc:  # pragma: no cover - defensive
            print("[scheduler] site-log sync crashed: %s: %s" % (type(exc).__name__, exc))
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
