# Credits, costs and pricing

There are two numbers, and they never mix:

* **Cost** - what the provider charges us. The rate card holds it, and every usage event
  is recorded in credits at that cost.
* **Sale price** - what a credit sells for. One setting, `CREDIT_PRICE_USD` in
  Settings -> Usage & credits, currently **$0.05**.

Credits are cost units. A call that costs $0.001 uses one credit; selling that credit for
$0.05 does not change the meter, the ledger or the reconciliation - it only changes what
the operator reads. Clients never see a dollar figure at all.

## Units

| Unit | Value | Where |
|---|---|---|
| 1 credit (cost) | $0.001 of provider spend | `rates.CREDITS_PER_USD` |
| 1 milli-credit | $0.000001 (what the database stores) | `rates.MILLI_PER_CREDIT` |
| Sale price | **$0.05 per credit** | `CREDIT_PRICE_USD` |
| Markup over cost | **4,900%** | `usage.credit_markup_percent()` |
| Gross margin | **98%** | `usage.credit_margin_percent()` |

The rate card keeps **dollars exactly as the provider dashboard shows them**. Conversion
lives in one place so a price copied from an invoice is never pre-multiplied.

## What a model call costs

```
milli = round( fresh_tokens       x input_per_1m
             + cached_tokens      x effective(cached_input_per_1m)
             + cache_write_tokens x effective(cache_write_per_1m)
             + output_tokens      x effective(output_per_1m) )

credits = milli / 1000          cost_dollars = milli / 1,000,000
```

Both sides are per million: `tokens x $/1M = $ x 1,000,000 = milli-credits`.

> **credits per 1M tokens of a bucket = its $/1M price x 1000.**
> $0.27/1M input -> 270 credits per 1M. $1.10/1M output -> 1,100 per 1M.

`effective(field)` returns the field when it is non-zero, otherwise `input_per_1m`.
**A zero means "not filled in", never "free"** - fill every column or output is charged
at the input price.

### The four buckets

| Provider | Fresh input | Cached input | Cache write | Output |
|---|---|---|---|---|
| Anthropic | `input_tokens` (excludes cache) | `cache_read_input_tokens` | `cache_creation_input_tokens` | `output_tokens` |
| OpenAI | `prompt_tokens - cached_tokens` | `prompt_tokens_details.cached_tokens` | - | `completion_tokens` |
| DeepSeek | `prompt_cache_miss_tokens` | `prompt_cache_hit_tokens` | - | `completion_tokens` |

Reasoning tokens are stored for visibility and are not charged twice: providers already
count them inside output.

## Voice and images

```
voice:  milli = round( seconds / 60 x per_unit($ per minute) x 1,000,000 )
image:  milli = round( images        x per_unit($ per image)  x 1,000,000 )
```

A `per_unit` of 0 or missing means **unpriced**, not free. Voice is billed when the
session ends, at `min(reservation, server_elapsed_seconds + 2)`; a browser that never
reports back is settled by the scheduler at its full reservation.

## What the ledger records

* Every priced call writes a `usage_events` row (four buckets, price snapshot) and a
  `credit_ledger` row `reason='usage'`, `delta_milli = -credits_milli`.
* `credit_balances` caches the sum; the ledger is the truth and `usage.reconcile(org)`
  asserts they agree.
* Grants and top-ups are `reason='grant'`; corrections are `adjustment`.
* The monthly allowance is granted once per `(org, 'grant', 'YYYY-MM')`; unused credits
  roll over because the balance is cumulative.
* **The sale price is not written on an event.** `credits_milli` is cost, so changing
  `CREDIT_PRICE_USD` never rewrites history and never disturbs reconciliation.
* Charges apply under shadow enforcement, so a balance can go negative; enforcement
  decides whether new work is accepted. `USAGE_ENFORCE=hard` refuses at zero for a client
  with a ledger; a client with no balance row is never blocked.

## Worked cost examples

Prices below are placeholders; use your dashboard.

**One call** - 1M fresh input at $0.27/1M, 200k cached at $0.07/1M, 150k output at
$1.10/1M:

```
1,000,000 x 0.27 = 270,000
  200,000 x 0.07 =  14,000
  150,000 x 1.10 = 165,000
                 --------
                  449,000 milli = 449 credits = $0.449 of cost
```

**A job of 30 calls**, each 8k fresh / 12k cached / 1.5k output at those prices:

```
per call: 8,000x0.27 + 12,000x0.07 + 1,500x1.10 = 2,160 + 840 + 1,650 = 4,650 milli
job:      30 x 4.65 = 139.5 credits = $0.1395 of cost
```

## The sale price

One number, one place: `CREDIT_PRICE_USD` (Settings -> Usage & credits, default **0.05**).
It applies to every credit - model calls, voice, images, every client, every plan. There
is no per-plan markup; volume deals are a billing decision made outside the meter.

| | |
|---|---|
| Sale price | **$0.05 / credit** |
| Our cost of that credit | $0.001 |
| 200 credits | **$10.00** |
| $10 of our cost (10,000 credits) | **$500.00** |
| Gross profit on $10 of cost | $490.00 |
| Gross margin | **98%** |

```
cost_of_goods      = credits_used x $0.001
revenue            = credits_sold x CREDIT_PRICE_USD
markup_percent     = (price / 0.001 - 1) x 100
margin_percent     = (price - 0.001) / price x 100
```

Markup and margin are not the same number: 4,900% markup is 98% margin.

### What the current price bills

| Thing | Credits (cost) | Billed at $0.05 |
|---|---|---|
| 1M-token call (1M fresh @ $0.27, 200k cached, 150k out) | 449 | **$22.45** |
| Typical 30-call writing job ($0.14 of cost) | 139.5 | **$6.98** |
| 5-minute voice session ($0.50 of cost) | 500 | **$25.00** |
| 3 images ($0.12 of cost) | 120 | **$6.00** |
| One credit | 1 | **$0.05** |

### Plans

Allowances are in credits, so a plan is priced as `allowance x $0.05`:

| Credits per month | Price | Our cost at full use |
|---|---|---|
| 200 | $10 | $0.20 |
| 2,000 | $100 | $2.00 |
| 10,000 | $500 | $10.00 |

`USAGE_LOW_WATER` is in credits too: at $0.05, the default 1,000 credits is $50 of
balance. Set it as a share of the smallest plan (20 credits for the 200-credit plan).

### What the client sees

Credits only. The client Usage page shows balance, plan, allowance, spend by model and
recent calls, all in credits; it never states a dollar figure. `GET /api/usage` for a
non-owner omits `credit_usd`, `markup_percent` and `margin_percent` entirely, so the price
is not in the browser either. The operator (including while acting as a client) sees the
price, the markup and the margin on Platform -> Usage, plus a *Value* column (balance x
price) and the rate card in provider cost.

### Changing the price

* It affects every credit immediately, but **no stored number changes**: credits are cost
  units and `credits_milli` is untouched.
* Dollar values shown for past months are computed at the current price, because the
  price is not snapshotted per event. If you ever need historical dollar values, that is
  a new column, not a re-read of old rows.
* Reconciliation is unaffected: it compares provider invoices to credits at cost.

### Two rules that keep the meter trustworthy

1. **Never edit the rate card to reflect the sale price.** Keep it at provider cost; the
   price is the one setting above. A marked-up rate card makes every reconciliation
   variance equal to the markup.
2. **Close the leaks before launch.** *Unpriced models* must be empty, and voice needs a
   `per_unit` row keyed to `voice.model()` with `kind=voice`. Until then those calls are
   free, at any price.

## Before you publish prices

- [ ] Fill the rate card from the dashboard (`python -m rates import rates.json`, or
      Platform -> Usage -> Rate card).
- [ ] Confirm no *Unpriced models* remain after a day of real work.
- [ ] Add the voice row: `provider=gemini`,
      `model=$(python -c "import voice; print(voice.model())")`, `kind=voice`, `per_unit`.
- [ ] Set `CREDIT_PRICE_USD` and the plan allowances in Settings -> Usage & credits.
- [ ] Set `USAGE_LOW_WATER` in credits, on the new scale.
- [ ] Grant a test client credits and check the sidebar bar, Workspace -> Usage and the
      operator's Value column.
- [ ] Run one real month, then `python -m usage reconcile <provider> YYYY-MM <billed>`.
- [ ] Only then set `USAGE_ENFORCE=hard`.

## Where it lives

| What | Where |
|---|---|
| Units, rate lookup, model arithmetic | `rates.py` (`MILLI_PER_USD`, `rate_for`, `price_model`, `price_unit`) |
| Credits, ledger, balance, grants | `usage.py` (`record_model`, `_charge`, `grant`, `added_this_month`) |
| Sale price, markup, margin | `usage.credit_price`, `credit_markup_percent`, `credit_margin_percent`; `CREDIT_PRICE_USD` |
| Operator-only fields | `app._org_usage(..., pricing=...)` |
| Token normalisation | `usage.normalise_openai`, `usage.normalise_anthropic` |
| Rates in the console | Platform -> Usage -> Rate card; `GET / POST /api/usage/rates` |
| Allowances and enforcement | Settings -> Usage & credits; `USAGE_PLAN_*`, `USAGE_LOW_WATER`, `USAGE_ENFORCE` |
| Reconciliation | Platform -> Usage -> *Reconcile against the invoice*; `python -m usage reconcile` |
