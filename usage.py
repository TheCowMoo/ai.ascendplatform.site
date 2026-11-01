"""What each call cost, attributed to the client and the job that caused it.

The number this module exists to get right is not "tokens" - it is *which* tokens.
Providers price fresh input, cached input, cache writes and output differently, and
they disagree about what a total means:

* Anthropic reports ``input_tokens`` **excluding** cache reads and writes;
* OpenAI-compatible providers report a ``prompt_tokens`` total that **includes** the
  cached subset;
* DeepSeek names its split ``prompt_cache_hit_tokens`` / ``prompt_cache_miss_tokens``.

So the raw response is normalised here into four buckets, and priced per bucket. Folding
them into one total would overcharge every cache-read call and undercharge every cache
write - which on an agent's tool loop is most of the bill.

Every function that writes swallows database errors and prints one line: a broken meter
must never lose an answer. ``guard`` is the only function that raises, and only when
enforcement is on and the client has a balance row showing nothing left.
"""

import contextlib
import contextvars
import json
import os

from db import IntegrityError, get_connection, now_str

import rates

CURRENT = contextvars.ContextVar("usage_context", default=None)


class CreditExhausted(RuntimeError):
    """Raised when a client with no credits left tries to start billable work."""


def _int(value, default=0):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _get(source, name, default=0):
    """A field from an SDK object or a plain dict - tests use dicts."""
    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _as_dict(row):
    return {key: row[key] for key in row.keys()}


# ---------------------------------------------------------------------------
# Attribution
# ---------------------------------------------------------------------------
@contextlib.contextmanager
def bind(org_id=None, user_id=None, job_id=None):
    """Carry the client/job through one job run.

    A model call does not know who asked for it; the job does. Everything recorded
    inside this block is attributed to that org, user and job, and cleared afterwards so
    a later call cannot inherit it.
    """
    token = CURRENT.set({"org_id": org_id, "user_id": user_id, "job_id": job_id})
    try:
        yield
    finally:
        CURRENT.reset(token)


def current():
    return dict(CURRENT.get() or {})


def _context(org_id=None, user_id=None, job_id=None):
    here = current()
    return {
        "org_id": org_id if org_id is not None else here.get("org_id"),
        "user_id": user_id if user_id is not None else here.get("user_id"),
        "job_id": job_id if job_id is not None else here.get("job_id"),
    }


# ---------------------------------------------------------------------------
# Normalising what the provider returned
# ---------------------------------------------------------------------------
def normalise_openai(usage, model=""):
    """Split an OpenAI-compatible usage object into billable buckets.

    Handles the three shapes seen in the wild: the OpenAI total+details, DeepSeek's
    cache hit/miss pair (which may arrive as extra fields), and a bare total.
    """
    prompt = _int(_get(usage, "prompt_tokens", 0))
    completion = _int(_get(usage, "completion_tokens", 0))
    details = _get(usage, "prompt_tokens_details", None)
    cached = _int(_get(details, "cached_tokens", 0)) if details else 0

    extra = _get(usage, "model_extra", None) or {}
    hit = _int(_get(usage, "prompt_cache_hit_tokens", 0)) or _int(_get(extra, "prompt_cache_hit_tokens", 0))
    miss = _int(_get(usage, "prompt_cache_miss_tokens", 0)) or _int(_get(extra, "prompt_cache_miss_tokens", 0))

    if hit or miss:
        input_cached = hit
        input_uncached = miss or max(0, prompt - hit)
    else:
        input_cached = min(cached, prompt)
        input_uncached = max(0, prompt - input_cached)

    out_details = _get(usage, "completion_tokens_details", None)
    reasoning = _int(_get(out_details, "reasoning_tokens", 0)) if out_details else 0

    return {
        "model": str(model or ""),
        "input_uncached": input_uncached,
        "input_cached": input_cached,
        "cache_write": 0,
        "output": completion,
        # Reasoning is already inside completion_tokens for these providers; recorded
        # so a report can show it, never added again at pricing time.
        "reasoning": reasoning,
        "seen": bool(prompt or completion),
    }


def normalise_anthropic(usage, model=""):
    """Split an Anthropic usage object. Its input total excludes both cache fields."""
    return {
        "model": str(model or ""),
        "input_uncached": _int(_get(usage, "input_tokens", 0)),
        "input_cached": _int(_get(usage, "cache_read_input_tokens", 0)),
        "cache_write": _int(_get(usage, "cache_creation_input_tokens", 0)),
        "output": _int(_get(usage, "output_tokens", 0)),
        "reasoning": 0,
        "seen": True,
    }


def normalise_autogen(usage, model=""):
    """The fallback: AutoGen's folded pair, when the raw response was not captured."""
    prompt = _int(_get(usage, "prompt_tokens", 0))
    completion = _int(_get(usage, "completion_tokens", 0))
    return {
        "model": str(model or ""),
        "input_uncached": prompt,
        "input_cached": 0,
        "cache_write": 0,
        "output": completion,
        "reasoning": 0,
        "seen": bool(prompt or completion),
    }


def _insert_event(kind, provider, model_configured, model_returned, label, split,
                  quantity, unit, usage_source, credits_milli, rate, org_id, user_id,
                  job_id, meta=None):
    try:
        with get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO usage_events (org_id, user_id, job_id, kind, provider, "
                "model_configured, model_returned, label, input_uncached, input_cached, "
                "cache_write, output_tokens, reasoning_tokens, quantity, unit, "
                "usage_source, credits_milli, rate_json, meta_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (org_id, user_id, job_id, kind, provider, model_configured or "",
                 model_returned or "", label or "",
                 _int(split.get("input_uncached")), _int(split.get("input_cached")),
                 _int(split.get("cache_write")), _int(split.get("output")),
                 _int(split.get("reasoning")), _int(quantity), unit,
                 usage_source or "raw", credits_milli,
                 json.dumps(rate, sort_keys=True) if rate else "",
                 json.dumps(meta, sort_keys=True) if meta else ""),
            )
            return cur.lastrowid
    except Exception as exc:
        print("[usage] could not record a %s event (%s: %s)"
              % (kind, type(exc).__name__, exc))
        return None


def record_model(provider, model_configured="", model_returned="", label="",
                 split=None, usage_source="raw", org_id=None, user_id=None,
                 job_id=None, meta=None):
    """Record one model call and charge it. Returns the event id (or None).

    ``model_returned`` is preferred for pricing: a provider may answer with a dated
    version of the alias that was configured, and that is the model on the invoice.
    """
    split = split or {}
    where = _context(org_id, user_id, job_id)
    credits_milli, rate = rates.price_model(provider, model_returned or model_configured,
                                            split)
    event_id = _insert_event(
        "model", provider, model_configured, model_returned, label, split,
        0, "tokens", usage_source, credits_milli, rate,
        where["org_id"], where["user_id"], where["job_id"], meta)
    _charge(where["org_id"], credits_milli, label)
    return event_id


def record_voice(org_id=None, user_id=None, seconds=0, provider="gemini",
                 model="", label="voice"):
    """Record spoken seconds. The price is per minute; the caller counts seconds."""
    where = _context(org_id, user_id, None)
    credits_milli, rate = rates.price_unit(provider, model, "voice", seconds)
    event_id = _insert_event(
        "voice", provider, model, "", label, {}, seconds, "seconds", "raw",
        credits_milli, rate, where["org_id"], where["user_id"], where["job_id"])
    _charge(where["org_id"], credits_milli, label)
    return event_id


def record_image(org_id=None, user_id=None, count=1, provider="gemini",
                 model="", label="image"):
    where = _context(org_id, user_id, None)
    credits_milli, rate = rates.price_unit(provider, model, "image", count)
    event_id = _insert_event(
        "image", provider, model, "", label, {}, count, "image", "raw",
        credits_milli, rate, where["org_id"], where["user_id"], where["job_id"])
    _charge(where["org_id"], credits_milli, label)
    return event_id


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------
def _apply_delta(org_id, delta_milli, reason, usage_id=None, actor_id=None,
                 note="", period=""):
    """Append to the ledger and move the cached balance in the same transaction."""
    delta_milli = int(delta_milli or 0)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO credit_ledger (org_id, delta_milli, reason, usage_id, "
            "actor_id, note, period) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (org_id, delta_milli, reason, usage_id, actor_id, note or "", period or ""),
        )
        moved = conn.execute(
            "UPDATE credit_balances SET balance_milli = balance_milli + ?, updated_at = ? "
            "WHERE org_id = ?", (delta_milli, now_str(), org_id))
        if not getattr(moved, "rowcount", 0):
            try:
                conn.execute(
                    "INSERT INTO credit_balances (org_id, balance_milli, updated_at) "
                    "VALUES (?, ?, ?)", (org_id, delta_milli, now_str()))
            except IntegrityError:
                # Another worker created the row between the UPDATE and the INSERT.
                conn.execute(
                    "UPDATE credit_balances SET balance_milli = balance_milli + ?, "
                    "updated_at = ? WHERE org_id = ?",
                    (delta_milli, now_str(), org_id))
    return delta_milli


def _charge(org_id, credits_milli, label=""):
    if org_id is None or not credits_milli:
        return
    try:
        _apply_delta(org_id, -int(credits_milli), "usage", note=label)
    except Exception as exc:
        print("[usage] could not charge org %s (%s: %s)"
              % (org_id, type(exc).__name__, exc))


def grant(org_id, credits_amount, reason="grant", actor_id=None, note="", period=""):
    """Add credits. ``credits_amount`` is in credits, not milli."""
    _apply_delta(org_id, int(round(float(credits_amount) * rates.MILLI_PER_CREDIT)),
                 reason, actor_id=actor_id, note=note, period=period)
    return balance(org_id)


def adjust(org_id, credits_amount, actor_id=None, note="", period=""):
    """A signed correction, for a refund or a manual fix."""
    return grant(org_id, credits_amount, reason="adjustment", actor_id=actor_id,
                 note=note, period=period)


def balance(org_id):
    """Milli-credits left, or None when this client is not on the credit system."""
    if org_id is None:
        return None
    with get_connection() as conn:
        row = conn.execute(
            "SELECT balance_milli FROM credit_balances WHERE org_id = ?", (org_id,)
        ).fetchone()
    return int(row["balance_milli"]) if row else None


def balance_credits(org_id):
    return rates.credits(balance(org_id))


def job_cost(job_id):
    """``(credits, unpriced_calls)`` for one job, or ``(None, 0)`` when it spent nothing."""
    if not job_id:
        return None, 0
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(credits_milli), 0) AS milli, COUNT(*) AS calls, "
            "SUM(CASE WHEN credits_milli IS NULL THEN 1 ELSE 0 END) AS unpriced "
            "FROM usage_events WHERE job_id = ?", (job_id,)).fetchone()
    if not row or not row["calls"]:
        return None, 0
    return rates.credits(int(row["milli"])), int(row["unpriced"] or 0)


def reconcile(org_id):
    """The cached balance against the ledger. They must agree; a drift is a bug."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(delta_milli), 0) AS total FROM credit_ledger "
            "WHERE org_id = ?", (org_id,)).fetchone()
    ledger = int(row["total"]) if row else 0
    cached = balance(org_id)
    return {"org_id": org_id, "ledger": ledger, "cached": cached,
            "ok": cached is None or cached == ledger}


# ---------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------
def enforcement_mode():
    """``shadow`` records only; ``hard`` refuses new work at zero.

    An env setting first, because phase 1 ships before the Settings UI exists. An
    unknown value reads as shadow: the safe direction is "do not block a client".
    """
    value = ""
    try:
        import settings

        value = settings.get("USAGE_ENFORCE", "")
    except Exception:
        value = ""
    value = str(value or os.environ.get("USAGE_ENFORCE") or "shadow").strip().lower()
    return value if value in ("shadow", "warn", "hard") else "shadow"


def guard(org_id=None, estimated_milli=0):
    """Refuse billable work when the client is out of credits and hard mode is on.

    A client with **no balance row** is not on the credit system yet, so it is never
    blocked: switching this on must not stop existing work the day it deploys.
    """
    target = org_id if org_id is not None else current().get("org_id")
    if target is None:
        return True
    milli = balance(target)
    if milli is None:
        return True
    mode = enforcement_mode()
    if mode == "hard" and milli - int(estimated_milli or 0) <= 0:
        raise CreditExhausted(
            "This client has no credits left (%s). Add credits to start new work."
            % rates.credits(milli))
    if mode in ("warn", "hard") and milli <= 0:
        print("[usage] org %s is at zero credits" % target)
    return True


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def summary(org_id=None, days=30):
    """Spend by kind/provider/model/label for the last N days."""
    import datetime

    since = (datetime.datetime.utcnow() - datetime.timedelta(days=max(1, int(days)))
             ).strftime("%Y-%m-%d %H:%M:%S")
    where = "WHERE created_at >= ?"
    params = [since]
    if org_id is not None:
        where += " AND org_id = ?"
        params.append(org_id)
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT kind, provider, "
            "COALESCE(NULLIF(model_returned, ''), model_configured) AS model, "
            "label, COUNT(*) AS calls, SUM(input_uncached) AS input_uncached, "
            "SUM(input_cached) AS input_cached, SUM(cache_write) AS cache_write, "
            "SUM(output_tokens) AS output_tokens, SUM(quantity) AS quantity, "
            "SUM(COALESCE(credits_milli, 0)) AS credits_milli, "
            "SUM(CASE WHEN credits_milli IS NULL THEN 1 ELSE 0 END) AS unpriced "
            "FROM usage_events %s GROUP BY kind, provider, model, label "
            "ORDER BY credits_milli DESC, calls DESC" % where,
            tuple(params),
        ).fetchall()
    rows = [_as_dict(row) for row in rows]
    total = sum(int(row["credits_milli"] or 0) for row in rows)
    return {
        "org_id": org_id,
        "days": days,
        "rows": rows,
        "milli": total,
        "credits": rates.credits(total),
        "balance": balance_credits(org_id),
        "unpriced": sum(int(row["unpriced"] or 0) for row in rows),
    }


def recent(org_id=None, limit=50):
    query = "SELECT * FROM usage_events"
    params = []
    if org_id is not None:
        query += " WHERE org_id = ?"
        params.append(org_id)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, int(limit)))
    with get_connection() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_as_dict(row) for row in rows]


def calibration_report():
    """One tiny live call per configured provider; prints the split we captured.

    This is how the operator confirms a provider's real shape before trusting the rate
    card: whether Gemini reports cache tokens, whether a compat shim folds reasoning
    into completion, and which model string comes back. Costs a fraction of a cent.

        python -m usage calibrate
    """
    import asyncio

    import llm
    from autogen_core.models import UserMessage

    async def probe(provider):
        client = llm.get_model_client(provider, usage_label="calibrate")
        await client.create([UserMessage(content="Reply with OK.", source="user")])
        capture = getattr(client, "_usage_capture", None)
        return capture.take() if capture else {}

    report = {}
    for provider in llm.configured_providers():
        try:
            report[provider] = asyncio.run(probe(provider))
        except Exception as exc:
            report[provider] = {"error": "%s: %s" % (type(exc).__name__, exc)}
    return report


def _main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: python -m usage show [org_id] | calibrate | "
              "grant <org_id> <credits> [note]")
        return 0
    if argv[0] == "show":
        org = int(argv[1]) if len(argv) > 1 else None
        data = summary(org)
        print("balance: %s credits | last %s days: %s credits"
              % (data["balance"], data["days"], data["credits"]))
        for row in data["rows"]:
            print("%-5s %-10s %-30s %-10s calls %-4d in %-8d cached %-8d out %-8d"
                  " -> %s credits"
                  % (row["kind"], row["provider"], row["model"] or "-",
                     row["label"] or "-", row["calls"] or 0,
                     row["input_uncached"] or 0, row["input_cached"] or 0,
                     row["output_tokens"] or 0, rates.credits(row["credits_milli"])))
        if data["unpriced"]:
            print("%d call(s) have no rate yet - python -m rates import rates.json"
                  % data["unpriced"])
        return 0
    if argv[0] == "calibrate":
        for provider, report in calibration_report().items():
            print(provider, report)
        return 0
    if argv[0] == "grant" and len(argv) > 2:
        new_balance = grant(int(argv[1]), float(argv[2]),
                            note=argv[3] if len(argv) > 3 else "")
        print("balance now %s credits" % rates.credits(new_balance))
        return 0
    print("unknown command")
    return 2


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
