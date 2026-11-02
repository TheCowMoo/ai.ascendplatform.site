# Credits, costs and pricing

The meter runs at **cost**. `rates.py` stores the provider's own list prices (dollars per
million tokens, per minute, per image) and converts them to credits; nothing in the meter
adds a margin. What you charge is a separate decision made on the plan or the top-up -
see [Pricing](#pricing).

## Units

| Unit | Value | Constant |
|---|---|---|
| 1 credit | $0.001 | `rates.CREDITS_PER_USD` |
| 1 milli-credit | $0.000001 (what the database stores) | `rates.MILLI_PER_CREDIT` |
| $1 | 1,000 credits / 1,000,000 milli | `rates.MILLI_PER_USD` |

The rate card keeps **dollars exactly as the dashboard shows them**. The conversion lives
in one place so a price copied from an invoice never has to be pre-multiplied.

## What a model call costs

```
milli = round( fresh_tokens       x input_per_1m
             + cached_tokens      x effective(cached_input_per_1m)
             + cache_write_tokens x effective(cache_write_per_1m)
             + output_tokens      x effective(output_per_1m) )

credits = milli / 1000          dollars = milli / 1,000,000
```

Both sides are per million, so `tokens x $/1M = $ x 1,000,000 = milli-credits`.

> **credits per 1M tokens of a bucket = its dollar price per 1M x 1000.**
> $0.27/1M input -> 270 credits per 1M tokens. $1.10/1M output -> 1,100 per 1M.

`effective(field)` returns the field when it is non-zero, otherwise `input_per_1m`. **A
zero means "not filled in", never "free"** - so fill every column, or output is charged at
the input price.

### The four buckets

Providers disagree about what their totals include, so the meter normalises to four
buckets before pricing:

| Provider | Fresh input | Cached input | Cache write | Output |
|---|---|---|---|---|
| Anthropic | `input_tokens` (excludes cache) | `cache_read_input_tokens` | `cache_creation_input_tokens` | `output_tokens` |
| OpenAI | `prompt_tokens - cached_tokens` | `prompt_tokens_details.cached_tokens` | - | `completion_tokens` |
| DeepSeek | `prompt_cache_miss_tokens` | `prompt_cache_hit_tokens` | - | `completion_tokens` |

Reasoning tokens are stored for visibility and are **not charged twice**: providers
already count them inside output.

## Voice and images

```
voice:  milli = round( seconds / 60 x per_unit($ per minute) x 1,000,000 )
image:  milli = round( images        x per_unit($ per image)  x 1,000,000 )
```

A `per_unit` of 0 or missing means **unpriced**, not free - unlike model fields, units have
no fallback. Voice is billed when the session ends, at
`min(reservation, server_elapsed_seconds + 2)`; a browser that never reports back is
settled by the scheduler at its full reservation.

## What the ledger records

* Every priced call writes a `usage_events` row (the four buckets, the price snapshot) and
  a `credit_ledger` row with `reason='usage'`, `delta_milli = -credits_milli`.
* `credit_balances` caches the sum; the ledger is the truth and `usage.reconcile(org)`
  asserts they agree.
* Grants and top-ups are `reason='grant'`; corrections are `adjustment`. A negative
  adjustment lowers the balance, a positive one raises it.
* The monthly allowance is granted once per `(org, 'grant', 'YYYY-MM')`, so the scheduler
  can run hourly without double-granting. Unused credits roll over; the balance is
  cumulative.
* Charges apply even under shadow enforcement, so a balance can go negative. The
  enforcement mode decides whether **new work is accepted**, not whether spending is
  recorded. `USAGE_ENFORCE=hard` refuses at zero for a client that has a ledger; a client
  with no balance row is never blocked.

## Worked examples

Prices below are placeholders; use your dashboard.

**One call** - 1M fresh input at $0.27/1M, 200k cached at $0.07/1M, 150k output at
$1.10/1M:

```
1,000,000 x 0.27 = 270,000
  200,000 x 0.07 =  14,000
  150,000 x 1.10 = 165,000
                 --------
                  449,000 milli = 449 credits = $0.449
```

**A job of 30 calls**, each 8k fresh / 12k cached / 1.5k output at those prices:

```
per call: 8,000x0.27 + 12,000x0.07 + 1,500x1.10 = 2,160 + 840 + 1,650 = 4,650 milli
job:      30 x 4.65 = 139.5 credits = $0.1395
```

**Voice** - 5 minutes at $0.10/min: `5 x 100 = 500 credits = $0.50` (100 credits/min).
**Images** - 3 at $0.04: `3 x 40 = 120 credits = $0.12`.

A cache hit is the difference between `12,000 x 0.07 = 840` and `12,000 x 0.27 = 3,240`
milli on that call: the four buckets exist so a cached agent loop is not billed as if every
token were fresh.

## Where the numbers come from

* `rates.rate_for(provider, model, kind)` tries the returned model name, the part after a
  `provider/` gateway prefix, then the name with a trailing `-YYYYMMDD` stripped.
* No row -> `credits_milli` is NULL, nothing is charged, and the call appears in
  `rates.missing_rates()` / *Unpriced models*. A guessed price would be worse than a gap.
* `python -m usage calibrate` makes one tiny live call per provider and prints the raw
  split, which is the fastest way to fill the card the first time.
* Editing a rate never rewrites history: every event snapshots the credits it cost.

## Pricing

Because 1 credit is exactly $0.001 of provider spend:

```
cost_of_goods      = credits_used x $0.001
revenue_per_credit = plan_price / included_credits
gross_margin       = 1 - 0.001 / revenue_per_credit
price_for_margin m = cost_of_goods / (1 - m)
```

| Sell price per credit | Margin | A 50,000-credit plan sells for |
|---|---|---|
| $0.0015 | 33.3% | $75 |
| $0.0020 | 50.0% | $100 |
| $0.0030 | 66.7% | $150 |
| $0.0040 | 75.0% | $200 |

Utilization moves the margin more than list price does. A 50,000-credit plan at $99 costs
$50 if fully used (49.5% margin) and $20 at 40% use (79.8%). Break-even at 40% use is $20:
never price below `0.001 x expected_used_credits`.

A defensible starting structure (an example, not a recommendation from the code):

| Plan | Credits/mo | Price | $/credit | Cost at full use | Margin at full use |
|---|---|---|---|---|---|
| Starter | 20,000 | $49 | $0.00245 | $20 | 59.2% |
| Growth | 75,000 | $149 | $0.00199 | $75 | 49.7% |
| Scale | 250,000 | $399 | $0.00160 | $250 | 37.3% |

Top-ups: 10,000 credits for $25 is $0.0025/credit (60% margin). Pick a minimum, and say
whether credits expire - the balance does not expire by itself.

`USAGE_LOW_WATER` drives both the banner and the sidebar's red state. Set it to roughly
10% of the smallest allowance (2,000 on the Starter plan above) so the gauge warns before
`hard` enforcement starts refusing work.

### Two rules that keep the meter trustworthy

1. **Keep the rate card at cost.** If you multiply it to bake in margin, the invoice
   reconciliation compares your marked-up number to the provider's bill and every
   "variance" becomes your markup. Price the plan or the top-up instead.
2. **Close the leaks before launch.** *Unpriced models* must be empty, and voice needs a
   `per_unit` row keyed to `voice.model()`, `kind=voice`. Until then those calls are free.

## Before you publish prices

- [ ] Fill the rate card from the dashboard (`python -m rates import rates.json`, or
      Platform -> Usage -> Rate card).
- [ ] Confirm no *Unpriced models* remain after a day of real work.
- [ ] Add the voice row: `provider=gemini`,
      `model=$(python -c "import voice; print(voice.model())")`, `kind=voice`,
      `per_unit` = $/min.
- [ ] Set the plan allowances and `USAGE_LOW_WATER` in Settings -> Usage & credits.
- [ ] Decide the sell price per credit, then the plan price from the table above.
- [ ] Grant a test client credits and watch Workspace -> Usage and the sidebar bar.
- [ ] Run one real month, then `python -m usage reconcile <provider> YYYY-MM <billed>`.
- [ ] Only then set `USAGE_ENFORCE=hard`.

## Where it lives

| What | Where |
|---|---|
| Units, rate lookup, model arithmetic | `rates.py` (`MILLI_PER_USD`, `rate_for`, `price_model`, `price_unit`) |
| Events, ledger, balance, grants | `usage.py` (`record_model`, `_charge`, `grant`, `added_this_month`) |
| Token normalisation | `usage.normalise_openai`, `usage.normalise_anthropic` |
| Rates in the console | Platform -> Usage -> Rate card; `GET / POST /api/usage/rates` |
| Allowances and enforcement | Settings -> Usage & credits; `USAGE_PLAN_*`, `USAGE_LOW_WATER`, `USAGE_ENFORCE` |
| Reconciliation | Platform -> Usage -> *Reconcile against the invoice*; `python -m usage reconcile` |
