"""The price list, in dollars, and the arithmetic that turns tokens into credits.

Providers publish prices in dollars per million tokens, per minute and per image, and
they price cached input differently from fresh input. So the rate card stores **dollars
exactly as the dashboard shows them** - copy the number, do not convert it - and the
conversion lives here, in one place.

Units:
    1 credit       = $0.001
    1 milli-credit = $0.000001        (what the database stores)

That choice keeps cheap calls out of the rounding bin:

    milli = tokens x dollars_per_1M      (1M tokens x $R/M = $R = R million milli)

No prices are seeded here on purpose. A guessed price is worse than a missing one:
``price_model`` returns None until a rate exists, the event records that it is unpriced,
and the operator fills the card from their own dashboard. Changing a rate only affects
future events, because every event snapshots the credits it cost.
"""

import json
import re

from db import get_connection

CREDITS_PER_USD = 1000
MILLI_PER_CREDIT = 1000
MILLI_PER_USD = CREDITS_PER_USD * MILLI_PER_CREDIT

KINDS = ("model", "voice", "image")

FIELDS = ("input_per_1m", "cached_input_per_1m", "cache_write_per_1m",
          "output_per_1m", "per_unit", "note")


def _as_dict(row):
    return {key: row[key] for key in row.keys()}


def credits(milli):
    """The display number: milli-credits as credits, three decimals."""
    if milli is None:
        return None
    return round(float(milli) / MILLI_PER_CREDIT, 3)


def set_rate(provider, model, kind="model", **values):
    """Create or replace one price row. ``values`` are FIELDS, in dollars."""
    provider = (provider or "").strip().lower()
    model = (model or "").strip()
    kind = (kind or "model").strip().lower()
    if not provider or not model:
        raise ValueError("a rate needs a provider and a model")
    if kind not in KINDS:
        raise ValueError("kind must be one of: %s" % ", ".join(KINDS))
    payload = {field: values.get(field, 0) for field in FIELDS[:-1]}
    payload["note"] = str(values.get("note") or "")
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM rate_card WHERE provider = ? AND model = ? AND kind = ?",
            (provider, model, kind),
        ).fetchone()
        if existing:
            assignments = ", ".join("%s = ?" % field for field in FIELDS)
            conn.execute(
                "UPDATE rate_card SET %s WHERE id = ?" % assignments,
                tuple(payload[field] for field in FIELDS) + (existing["id"],),
            )
            row = conn.execute(
                "SELECT * FROM rate_card WHERE id = ?", (existing["id"],)).fetchone()
            return _as_dict(row)
        conn.execute(
            "INSERT INTO rate_card (provider, model, kind, %s) VALUES (?, ?, ?, %s)"
            % (", ".join(FIELDS), ", ".join("?" for _ in FIELDS)),
            tuple([provider, model, kind] + [payload[field] for field in FIELDS]),
        )
        row = conn.execute(
            "SELECT * FROM rate_card WHERE provider = ? AND model = ? AND kind = ?",
            (provider, model, kind),
        ).fetchone()
    return _as_dict(row)


def _candidates(model):
    """Model strings a rate may have been filed under, most specific first.

    A provider can return a dated name (``claude-sonnet-4-5-20250929``) for the alias
    that was configured, and the LiteLLM gateway rewrites ids to ``provider/model``.
    Both are the same price, so both are tried before reporting a missing rate.
    """
    base = (model or "").strip()
    values = []
    for value in (base, base.split("/", 1)[-1] if "/" in base else ""):
        value = value.strip()
        if value and value not in values:
            values.append(value)
        dated = re.sub(r"-\d{8}$", "", value) if value else ""
        if dated and dated not in values:
            values.append(dated)
    return values


def rate_for(provider, model, kind="model"):
    """The price row for this provider/model, or None."""
    provider = (provider or "").strip().lower()
    kind = (kind or "model").strip().lower()
    names = _candidates(model)
    if not provider or not names:
        return None
    marks = ", ".join("?" for _ in names)
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM rate_card WHERE provider = ? AND kind = ? AND model IN (%s)"
            % marks,
            tuple([provider, kind] + names),
        ).fetchall()
    by_model = {row["model"]: _as_dict(row) for row in rows}
    for name in names:
        if name in by_model:
            return by_model[name]
    return None


def _effective(rate, field):
    """A zero discount is not a discount.

    A provider that does not price cached input separately should not make it free, and
    a card filled in with only ``input_per_1m`` must not charge nothing for output. Any
    missing specific rate falls back to the base input rate - the conservative reading
    of "I did not fill this in".
    """
    value = float(rate.get(field) or 0)
    if value:
        return value
    return float(rate.get("input_per_1m") or 0)


def price_model(provider, model, split):
    """``(milli_credits, rate)`` for one model call, or ``(None, None)``.

    ``split`` carries ``input_uncached``, ``input_cached``, ``cache_write`` and
    ``output``. ``milli = tokens x dollars`` because both sides are per million.
    """
    rate = rate_for(provider, model, "model")
    if not rate:
        return None, None
    milli = (
        float(split.get("input_uncached") or 0) * float(rate.get("input_per_1m") or 0)
        + float(split.get("input_cached") or 0) * _effective(rate, "cached_input_per_1m")
        + float(split.get("cache_write") or 0) * _effective(rate, "cache_write_per_1m")
        + float(split.get("output") or 0) * _effective(rate, "output_per_1m")
    )
    return int(round(milli)), rate


def price_unit(provider, model, kind, quantity):
    """``(milli_credits, rate)`` for voice seconds or images."""
    rate = rate_for(provider, model, kind)
    if not rate:
        return None, None
    per_unit = float(rate.get("per_unit") or 0)
    if not per_unit:
        return None, None
    quantity = float(quantity or 0)
    if kind == "voice":
        # per_unit is dollars per minute; the caller counts seconds.
        dollars = quantity / 60.0 * per_unit
    else:
        dollars = quantity * per_unit
    return int(round(dollars * MILLI_PER_USD)), rate


def list_rates():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM rate_card ORDER BY provider, model, kind").fetchall()
    return [_as_dict(row) for row in rows]


def delete_rate(rate_id):
    with get_connection() as conn:
        conn.execute("DELETE FROM rate_card WHERE id = ?", (rate_id,))
    return True


def missing_rates():
    """Models that have been used but are not priced - the operator's to-do list."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT provider, model_returned, model_configured, COUNT(*) AS calls "
            "FROM usage_events WHERE kind = 'model' AND credits_milli IS NULL "
            "GROUP BY provider, model_returned, model_configured ORDER BY calls DESC"
        ).fetchall()
    return [_as_dict(row) for row in rows]


def load_json(path):
    """Import a rate card from a JSON list of rows. Returns how many were written.

    Shape: [{"provider": "deepseek", "model": "deepseek-chat",
             "input_per_1m": 0.27, "cached_input_per_1m": 0.07,
             "output_per_1m": 1.10, "note": "list price 2026-10"}]
    """
    with open(path, encoding="utf-8") as handle:
        rows = json.load(handle)
    if not isinstance(rows, list):
        raise ValueError("expected a JSON list of rate rows")
    written = 0
    for row in rows:
        set_rate(row.get("provider"), row.get("model"), row.get("kind") or "model",
                 **{field: row.get(field, 0) for field in FIELDS})
        written += 1
    return written


def _main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: python -m rates show | import <file.json>")
        return 0
    if argv[0] == "show":
        for row in list_rates():
            print("#%(id)s %(provider)s/%(model)s [%(kind)s] in %(input_per_1m)s "
                  "cached %(cached_input_per_1m)s write %(cache_write_per_1m)s "
                  "out %(output_per_1m)s unit %(per_unit)s  %(note)s" % row)
        return 0
    if argv[0] == "import" and len(argv) > 1:
        print("imported %d rate(s)" % load_json(argv[1]))
        return 0
    print("unknown command")
    return 2


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
