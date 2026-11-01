"""The meter: which tokens were billed, what they cost, and who pays.

Run: .\\venv\\Scripts\\python.exe tests\\test_usage.py

The number this suite protects is not "tokens" - it is *which* tokens. Providers price
fresh input, cached input, cache writes and output differently, and they disagree about
what their totals include (Anthropic excludes cache reads from input_tokens; OpenAI
includes the cached subset in prompt_tokens; DeepSeek names the split differently). Fold
them together and every cache-heavy agent loop is overcharged.

No network and no live key: the normalisers are driven with the shapes each SDK returns,
and the rate card is a test row.
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.pop("ALLOW_LIVE_ACTIONS", None)

import db  # noqa: E402
import orgs as orgs_service  # noqa: E402
import rates  # noqa: E402
import usage  # noqa: E402

# The app calls this at startup; a dev SQLite file predates migration 0021, and these
# tests are the first thing to touch the new tables.
db.init_db()

TOKEN = uuid.uuid4().hex[:6]
PROVIDER = "test-%s" % TOKEN
MODEL = "model-%s" % TOKEN
FAILED = []


def check(label, got, want):
    if got == want:
        print("  ok - %s" % label)
        return
    FAILED.append(label)
    print("  FAILED - %s: %r != %r" % (label, got, want))


def _org():
    # A distinct name per call: the slug is derived from it, and this suite creates
    # several clients with different balances.
    _org.counter += 1
    return orgs_service.create_org(
        {"name": "Usage Test %s %d" % (TOKEN, _org.counter)})


_org.counter = 0


def cleanup():
    with db.get_connection() as conn:
        orgs = conn.execute(
            "SELECT id FROM orgs WHERE name LIKE ?", ("%%Usage Test %s%%" % TOKEN,)
        ).fetchall()
        for org in orgs:
            org_id = org["id"]
            for table in ("usage_events", "credit_ledger", "credit_balances",
                          "voice_sessions"):
                conn.execute("DELETE FROM %s WHERE org_id = ?" % table, (org_id,))
            conn.execute("DELETE FROM users WHERE org_id = ?", (org_id,))
            conn.execute("DELETE FROM orgs WHERE id = ?", (org_id,))
        conn.execute("DELETE FROM rate_card WHERE provider LIKE ?",
                     ("test-%s%%" % TOKEN,))
        conn.execute("DELETE FROM usage_reconciliations WHERE provider LIKE ?",
                     ("test-%s%%" % TOKEN,))


def _rates():
    """A card with four different prices, so a folded total cannot pass by accident."""
    rates.set_rate(PROVIDER, MODEL, input_per_1m=3.0, cached_input_per_1m=0.3,
                   cache_write_per_1m=3.75, output_per_1m=15.0, note="test row")
    rates.set_rate(PROVIDER, "voice-%s" % TOKEN, kind="voice", per_unit=0.10)
    rates.set_rate(PROVIDER, "image-%s" % TOKEN, kind="image", per_unit=0.04)


def test_openai_shapes():
    openai_like = usage.normalise_openai(
        {"prompt_tokens": 1000, "completion_tokens": 200,
         "prompt_tokens_details": {"cached_tokens": 800}},
        model="gpt-test")
    check("openai cached is a subset of prompt", query(openai_like), (200, 800, 0, 200))
    check("the returned model is kept", openai_like["model"], "gpt-test")

    deepseek_like = usage.normalise_openai(
        {"prompt_tokens": 1000, "completion_tokens": 50,
         "prompt_cache_hit_tokens": 700, "prompt_cache_miss_tokens": 300})
    check("deepseek hit/miss is read, not prompt-total", query(deepseek_like),
          (300, 700, 0, 50))

    bare = usage.normalise_openai({"prompt_tokens": 100, "completion_tokens": 10})
    check("a bare total is all uncached", query(bare), (100, 0, 0, 10))
    print("openai-shaped usage is split three ways")


def test_anthropic_shape():
    split = usage.normalise_anthropic(
        {"input_tokens": 200, "output_tokens": 50,
         "cache_read_input_tokens": 800, "cache_creation_input_tokens": 100},
        model="claude-test")
    check("anthropic input excludes both cache fields", query(split), (200, 800, 100, 50))
    print("anthropic cache reads and writes are separated from input")


def query(split):
    return (split["input_uncached"], split["input_cached"],
            split["cache_write"], split["output"])


def test_pricing_counts_each_bucket():
    _rates()
    split = {"input_uncached": 200, "input_cached": 800,
             "cache_write": 100, "output": 50}
    milli, rate = rates.price_model(PROVIDER, MODEL, split)
    # 200x3 + 800x0.3 + 100x3.75 + 50x15 = 600 + 240 + 375 + 750
    check("the four buckets are priced separately", milli, 1965)
    check("the rate comes back for the snapshot", rate["input_per_1m"], 3.0)
    check("1965 milli is 1.965 credits", rates.credits(1965), 1.965)
    print("a cached call is not priced as if every token were fresh")


def test_a_zero_rate_is_not_free():
    _rates()
    rates.set_rate(PROVIDER, "flat-%s" % TOKEN, input_per_1m=2.0, output_per_1m=4.0)
    milli, _ = rates.price_model(PROVIDER, "flat-%s" % TOKEN, {"input_cached": 1000})
    check("an unfilled cached rate falls back to input", milli, 2000)


def test_a_missing_rate_is_not_guessed():
    _rates()
    milli, rate = rates.price_model(PROVIDER, "unpriced-%s" % TOKEN,
                                    {"input_uncached": 10})
    check("no rate -> no price", (milli, rate), (None, None))

    org = _org()
    event_id = usage.record_model(PROVIDER, "unpriced-%s" % TOKEN, "", "agent",
                                  {"input_uncached": 10, "output": 5},
                                  org_id=org["id"])
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT credits_milli FROM usage_events WHERE id = ?", (event_id,)).fetchone()
    check("the event records that it is unpriced", row["credits_milli"], None)
    check("nothing is charged", usage.balance(org["id"]), None)
    check("and the operator has a to-do list",
          any(item["model_configured"] == "unpriced-%s" % TOKEN
              for item in rates.missing_rates()), True)
    print("an unpriced model is flagged, never silently priced at the default")


def test_record_and_charge():
    _rates()
    org = _org()
    usage.grant(org["id"], 100)
    check("a grant creates the balance", usage.balance(org["id"]), 100000)

    split = {"input_uncached": 200, "input_cached": 800,
             "cache_write": 100, "output": 50}
    event_id = usage.record_model(PROVIDER, MODEL, MODEL, "manager", split,
                                  org_id=org["id"])
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM usage_events WHERE id = ?", (event_id,)).fetchone()
    check("the split is stored, not a total",
          (row["input_cached"], row["cache_write"], row["output_tokens"]),
          (800, 100, 50))
    check("the price is snapshotted on the event", row["credits_milli"], 1965)
    check("the balance moved by exactly that", usage.balance(org["id"]), 98035)
    check("ledger and cached balance agree", usage.reconcile(org["id"])["ok"], True)
    check("the summary reports the spend", usage.summary(org["id"])["credits"], 1.965)
    print("one call, one event, one ledger line, one balance")


def test_bind_attributes_to_the_job():
    _rates()
    org = _org()
    with usage.bind(org["id"], 7, 99):
        usage.record_model(PROVIDER, MODEL, MODEL, "agent", {"output": 1})
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT org_id, user_id, job_id FROM usage_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
    check("the job context is recorded",
          (row["org_id"], row["user_id"], row["job_id"]), (org["id"], 7, 99))

    usage.record_model(PROVIDER, MODEL, MODEL, "agent", {"output": 1})
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT org_id FROM usage_events ORDER BY id DESC LIMIT 1").fetchone()
    check("and cleared once the job ends", row["org_id"], None)
    print("usage belongs to the job that caused it, and to nothing afterwards")


def test_guard_never_blocks_a_client_off_the_system():
    _rates()
    org = _org()
    usage.grant(org["id"], 0.001)                      # one milli-credit
    usage.record_model(PROVIDER, MODEL, MODEL, "agent",
                       {"input_uncached": 1, "output": 1}, org_id=org["id"])
    original = usage.enforcement_mode
    try:
        usage.enforcement_mode = lambda: "shadow"
        check("shadow mode does not block", usage.guard(org["id"]), True)
        usage.enforcement_mode = lambda: "hard"
        try:
            usage.guard(org["id"])
            check("hard mode refuses at zero", False, True)
        except usage.CreditExhausted:
            check("hard mode refuses at zero", True, True)
    finally:
        usage.enforcement_mode = original
    check("no balance row means not on the credit system",
          usage.guard(_org()["id"]), True)
    print("enforcement is opt-in, and a client with no ledger is untouched")


def test_voice_and_image_units():
    _rates()
    org = _org()
    usage.record_voice(org_id=org["id"], provider=PROVIDER,
                       model="voice-%s" % TOKEN, seconds=300)
    usage.record_image(org_id=org["id"], provider=PROVIDER,
                       model="image-%s" % TOKEN, count=2)
    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT kind, credits_milli, unit FROM usage_events WHERE org_id = ? "
            "ORDER BY id", (org["id"],)).fetchall()
    check("five minutes at $0.10 is 500 credits",
          (rows[0]["kind"], rows[0]["credits_milli"], rows[0]["unit"]),
          ("voice", 500000, "seconds"))
    check("two images at $0.04 is 80 credits",
          (rows[1]["kind"], rows[1]["credits_milli"], rows[1]["unit"]),
          ("image", 80000, "image"))
    print("voice is priced per minute, images per image")


def test_raw_capture_reads_a_completion():
    import asyncio

    import llm

    class Usage:
        prompt_tokens = 1000
        completion_tokens = 200
        prompt_tokens_details = {"cached_tokens": 800}

    class Response:
        model = "model-2026"
        usage = Usage()

    class Completions:
        async def create(self, *args, **kwargs):
            return Response()

    class SDK:
        class Chat:
            completions = Completions()

        chat = Chat()

    class FakeClient:
        _client = SDK()

    capture = llm._instrument_openai_client(FakeClient())
    asyncio.run(FakeClient._client.chat.completions.create(messages=[]))
    split = capture.take()
    check("the wrapper keeps the cache split",
          (split["input_cached"], split["input_uncached"]), (800, 200))
    check("and the model the provider actually returned", split["model"], "model-2026")
    print("the SDK wrapper sees what AutoGen folds away")


def test_raw_capture_reads_a_stream():
    import asyncio

    import llm

    class Usage:
        prompt_tokens = 10
        completion_tokens = 5

    class Chunk:
        def __init__(self, usage=None):
            self.model = "stream-model"
            self.usage = usage

    class Stream:
        def __init__(self, chunks):
            self.chunks = list(chunks)
            self.closed = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self.chunks:
                raise StopAsyncIteration
            return self.chunks.pop(0)

        async def close(self):
            self.closed = True

    capture = llm.RawUsage()
    stream = Stream([Chunk(), Chunk(Usage())])

    async def consume():
        seen = []
        async for chunk in llm._tee_openai_stream(stream, capture):
            seen.append(chunk)
        return seen

    seen = asyncio.run(consume())
    split = capture.take()
    check("every chunk is passed through", len(seen), 2)
    check("the final usage is kept", (split["input_uncached"], split["output"]), (10, 5))
    check("and the stream is closed", stream.closed, True)
    print("a streamed reply is metered from its final chunk")


def test_anthropic_capture_keeps_cache_fields():
    import asyncio

    import llm

    class Usage:
        input_tokens = 200
        output_tokens = 1
        cache_read_input_tokens = 800
        cache_creation_input_tokens = 100

    class Response:
        model = "claude-x"
        usage = Usage()

    class Resource:
        async def create(self, *args, **kwargs):
            return Response()

    class Client:
        def __init__(self):
            self._client = type("SDK", (), {"messages": Resource()})()

    client = Client()
    llm._patch_anthropic_client(client)
    asyncio.run(client._client.messages.create(
        messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}]))
    split = client._usage_capture.take()
    check("cache reads and writes survive the patch",
          (split["input_cached"], split["cache_write"]), (800, 100))
    check("and plain input is the rest", split["input_uncached"], 200)
    print("the Claude patch records cache reads and cache writes")


def test_the_metered_client_records_and_falls_back():
    import asyncio

    import llm

    class Usage:
        prompt_tokens = 100
        completion_tokens = 20

    class Result:
        usage = Usage()

    class Base:
        async def create(self, *args, **kwargs):
            return Result()

    class Client(llm._MeteredMixin, Base):
        pass

    client = Client()
    client._usage_provider = PROVIDER
    client._usage_model = MODEL
    client._usage_label = "agent"
    # No capture installed: this also pins the fallback path, which must still record.
    org = _org()
    with usage.bind(org["id"], 7, 99):
        asyncio.run(client.create())
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM usage_events WHERE org_id = ? ORDER BY id DESC LIMIT 1",
            (org["id"],)).fetchone()
    check("the fallback still records the tokens",
          (row["input_uncached"], row["output_tokens"]), (100, 20))
    check("and says the raw capture was missing", row["usage_source"], "autogen-fallback")
    check("the label names the agent", row["label"], "agent")
    print("a client with no raw capture is metered, and marked as a fallback")


def test_voice_is_billed_when_it_closes():
    """A spoken session is charged for how long its token was open, not at mint.

    The browser holds a token, and the price comes from the server's clock: a client
    that posts "0 seconds" after a five-minute call must not get the minutes back.
    """
    _rates()
    org = _org()
    voice_model = "voice-%s" % TOKEN
    token = usage.open_voice_session(org["id"], 7, 300, provider=PROVIDER,
                                     model=voice_model)
    with db.get_connection() as conn:
        before = conn.execute(
            "SELECT COUNT(*) AS n FROM usage_events WHERE org_id = ?",
            (org["id"],)).fetchone()["n"]
    check("minting a session charges nothing", before, 0)
    check("another user cannot close it",
          usage.settle_voice_session(token, user_id=8), None)
    settled = usage.settle_voice_session(token, user_id=7)
    check("the owner closes it", settled is not None, True)
    check("and pays for the elapsed seconds, not the reservation",
          settled["billed_seconds"] < 300, True)
    check("a closed session cannot be settled twice",
          usage.settle_voice_session(token, user_id=7), None)
    with db.get_connection() as conn:
        event = conn.execute(
            "SELECT kind, quantity FROM usage_events WHERE org_id = ? "
            "ORDER BY id DESC LIMIT 1", (org["id"],)).fetchone()
    check("the event records the billed seconds",
          (event["kind"], event["quantity"]), ("voice", settled["billed_seconds"]))
    print("a spoken session is billed for how long it was open")

    # A tab that never comes back: the sweep bills the whole reservation, so a crashed
    # browser cannot be free. The row is aged by hand rather than waiting five minutes.
    abandoned = usage.open_voice_session(org["id"], 7, 300, provider=PROVIDER,
                                         model=voice_model)
    with db.get_connection() as conn:
        conn.execute("UPDATE voice_sessions SET created_at = ? WHERE id = ?",
                     ("2020-01-01 00:00:00", abandoned))
    usage.sweep_voice_sessions()
    with db.get_connection() as conn:
        row = conn.execute("SELECT status, billed_seconds FROM voice_sessions "
                           "WHERE id = ?", (abandoned,)).fetchone()
    check("the sweep bills an abandoned session at its reservation",
          (row["status"], row["billed_seconds"]), ("settled", 300))
    print("a session nobody closed is settled by the sweep")


def test_reconciliation_compares_the_meter_to_the_invoice():
    _rates()
    org = _org()
    usage.record_model(PROVIDER, MODEL, MODEL, "agent",
                       {"input_uncached": 1000000, "output": 0}, org_id=org["id"])
    period = db.now_str()[:7]
    computed = usage.computed_milli(PROVIDER, period)
    check("the month's cost is summed from the snapshot", computed > 0, True)

    row = usage.save_reconciliation(PROVIDER, period, computed / rates.MILLI_PER_USD)
    check("an invoice that matches the meter has no variance", row["variance_milli"], 0)
    check("and it reads back in dollars", row["variance_usd"], 0.0)

    row = usage.save_reconciliation(PROVIDER, period,
                                    computed / rates.MILLI_PER_USD + 1.0)
    check("a larger invoice is a shortfall, in milli-credits",
          row["variance_milli"], -rates.MILLI_PER_USD)
    check("the provider and month are stored as given",
          (row["provider"], row["period"]), (PROVIDER, period))

    rows = usage.reconciliations(2)
    check("saved months are listed newest first", rows[0]["id"], row["id"])

    try:
        usage.save_reconciliation(PROVIDER, "November", 1)
        check("a malformed month is refused", False, True)
    except ValueError:
        check("a malformed month is refused", True, True)
    print("a provider invoice is checked against what we charged")


def main():
    try:
        test_openai_shapes()
        test_anthropic_shape()
        test_pricing_counts_each_bucket()
        test_a_zero_rate_is_not_free()
        test_a_missing_rate_is_not_guessed()
        test_record_and_charge()
        test_bind_attributes_to_the_job()
        test_guard_never_blocks_a_client_off_the_system()
        test_voice_and_image_units()
        test_raw_capture_reads_a_completion()
        test_raw_capture_reads_a_stream()
        test_anthropic_capture_keeps_cache_fields()
        test_the_metered_client_records_and_falls_back()
        test_voice_is_billed_when_it_closes()
        test_reconciliation_compares_the_meter_to_the_invoice()
    finally:
        cleanup()
    if FAILED:
        print("\n%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
        raise SystemExit(1)
    print("\nUSAGE OK - cached input is priced as cached input, and nothing is guessed")


if __name__ == "__main__":
    main()
