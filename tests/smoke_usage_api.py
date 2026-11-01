"""The credit console: a client sees its own meter, the operator owns the money.

Run: .\\venv\\Scripts\\python.exe tests\\smoke_usage_api.py

What this pins:

* a client reads its own usage and balance, and nothing about another client;
* only the operator may grant credits or touch the rate card;
* a negative amount is an adjustment, not a top-up;
* the monthly allowance is granted once per period, even when the sweep runs again.
"""

import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.pop("ALLOW_LIVE_ACTIONS", None)

import app as appmod  # noqa: E402
import db  # noqa: E402
import orgs as orgs_service  # noqa: E402
import usage  # noqa: E402

TOKEN = uuid.uuid4().hex[:6]
FAILED = []


def check(label, got, want):
    if got == want:
        print("  ok - %s" % label)
        return
    FAILED.append(label)
    print("  FAILED - %s: %r != %r" % (label, got, want))


def _org(label):
    slug = "usage-api-%s-%s" % (label, TOKEN)
    email = "%s@usage.test" % slug
    org = orgs_service.create_org({
        "name": "Usage API %s %s" % (label, TOKEN),
        "email": email,
        "password": "usage-pass-1234",
    })
    org["email"] = email
    return org


def _login(org):
    client = appmod.app.test_client()
    resp = client.post("/api/auth/login",
                       json={"email": org["email"], "password": "usage-pass-1234"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return client


def _owner():
    client = appmod.app.test_client()
    resp = client.post("/api/auth/login",
                       json={"email": "admin@example.com", "password": "admin123"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return client


def cleanup():
    with db.get_connection() as conn:
        orgs = conn.execute(
            "SELECT id FROM orgs WHERE name LIKE ?", ("%%Usage API%%%s" % TOKEN,)
        ).fetchall()
        for org in orgs:
            org_id = org["id"]
            for table in ("usage_events", "credit_ledger", "credit_balances",
                          "voice_sessions"):
                conn.execute("DELETE FROM %s WHERE org_id = ?" % table, (org_id,))
            conn.execute("DELETE FROM users WHERE org_id = ?", (org_id,))
            conn.execute("DELETE FROM orgs WHERE id = ?", (org_id,))
        conn.execute("DELETE FROM rate_card WHERE provider LIKE ?",
                     ("testapi-%s%%" % TOKEN,))
        conn.execute("DELETE FROM usage_reconciliations WHERE provider LIKE ?",
                     ("testapi-%s%%" % TOKEN,))


def test_a_client_sees_only_its_own_meter(alpha, beta):
    owner = _owner()
    granted = owner.post("/api/orgs/%d/credits" % alpha["id"],
                         json={"credits": 25, "note": "test top-up"})
    check("the operator grants credits", granted.status_code, 200)
    check("the balance is reported", granted.get_json()["balance"], 25.0)

    owner_usage = owner.get("/api/orgs/%d/usage" % alpha["id"]).get_json()
    check("the operator sees the balance", owner_usage["balance"], 25.0)
    check("and the reconcile agrees", owner_usage["reconcile"]["ok"], True)
    check("the row carries the plan allowance", owner_usage["allowance"] >= 0, True)

    a = _login(alpha)
    mine = a.get("/api/usage").get_json()
    check("the client reads its own usage", mine["org_id"], alpha["id"])
    check("with its balance", mine["balance"], 25.0)
    check("and its own ledger", mine["ledger"][0]["delta_milli"], 25000)
    check("no other client appears", mine["org_id"] == beta["id"], False)

    negative = owner.post("/api/orgs/%d/credits" % alpha["id"],
                          json={"credits": -5, "note": "correction"})
    check("a negative amount is an adjustment", negative.get_json()["balance"], 20.0)
    check("zero is refused",
          owner.post("/api/orgs/%d/credits" % alpha["id"],
                     json={"credits": 0}).status_code, 400)
    print("a client sees its own meter; an adjustment is not a top-up")


def test_only_the_operator_moves_money(alpha, beta):
    a = _login(alpha)
    check("a client cannot grant credits",
          a.post("/api/orgs/%d/credits" % beta["id"], json={"credits": 100}).status_code,
          403)
    check("a client cannot set a rate",
          a.post("/api/usage/rates", json={"provider": "x", "model": "y"}).status_code,
          403)
    check("a client cannot read another client's usage",
          a.get("/api/orgs/%d/usage" % beta["id"]).status_code, 403)
    print("credits and rates are the operator's")


def test_the_rate_card_is_editable():
    owner = _owner()
    provider = "testapi-%s" % TOKEN
    saved = owner.post("/api/usage/rates", json={
        "provider": provider, "model": "model-a", "kind": "model",
        "input_per_1m": 3, "cached_input_per_1m": 0.3,
        "cache_write_per_1m": 3.75, "output_per_1m": 15,
    })
    check("a rate can be saved", saved.status_code, 201)
    listed = owner.get("/api/usage/rates").get_json()["rates"]
    found = [row for row in listed if row["provider"] == provider]
    check("and it is listed", len(found), 1)
    check("with the dollars unchanged", found[0]["output_per_1m"], 15.0)
    removed = owner.delete("/api/usage/rates/%d" % found[0]["id"])
    check("and deleted", removed.status_code, 200)
    check("the list is empty again",
          [row for row in owner.get("/api/usage/rates").get_json()["rates"]
           if row["provider"] == provider], [])
    print("the rate card is the operator's, in dollars")


def test_the_monthly_grant_is_idempotent(alpha):
    original_list = orgs_service.list_orgs
    original_allowance = usage.plan_allowance
    orgs_service.list_orgs = lambda limit=1000: [alpha]
    usage.plan_allowance = lambda plan: 100
    try:
        first = usage.grant_monthly(period="1999-01")
        second = usage.grant_monthly(period="1999-01")
    finally:
        orgs_service.list_orgs = original_list
        usage.plan_allowance = original_allowance
    check("the first sweep grants", first, 1)
    check("the second does not grant again", second, 0)
    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT COUNT(*) AS n FROM credit_ledger WHERE org_id = ? "
            "AND reason = 'grant' AND period = '1999-01'", (alpha["id"],)).fetchone()
    check("one ledger line for the period", rows["n"], 1)
    print("the monthly allowance lands once per period")


def test_a_voice_session_is_settled_by_its_owner(alpha, beta):
    """The browser posts the session token back; nobody else can close it."""
    a = _login(alpha)
    with db.get_connection() as conn:
        user_id = conn.execute("SELECT id FROM users WHERE org_id = ?",
                               (alpha["id"],)).fetchone()["id"]
    token = usage.open_voice_session(alpha["id"], user_id, 300, provider="gemini",
                                     model="voice-test-%s" % TOKEN)
    closed = a.post("/api/voice/usage", json={"session_id": token})
    check("the owner settles the session", closed.status_code, 200)
    check("the seconds come from the server's clock",
          closed.get_json()["billed_seconds"] < 300, True)
    check("a second post is refused",
          a.post("/api/voice/usage", json={"session_id": token}).status_code, 409)

    other = usage.open_voice_session(alpha["id"], user_id, 300)
    b = _login(beta)
    check("another client cannot close it",
          b.post("/api/voice/usage", json={"session_id": other}).status_code, 409)
    check("the owner still can",
          a.post("/api/voice/usage", json={"session_id": other}).status_code, 200)

    # sendBeacon posts text/plain; refusing that shape would leave the session open
    # until the sweep charges the whole reservation.
    beacon = usage.open_voice_session(alpha["id"], user_id, 300)
    plain = a.post("/api/voice/usage", data=json.dumps({"session_id": beacon}),
                   content_type="text/plain")
    check("a text/plain beacon is accepted", plain.status_code, 200)
    print("a spoken session is settled by the user who opened it")


def test_invoices_are_reconciled_by_the_operator(alpha):
    owner = _owner()
    a = _login(alpha)
    body = {"provider": "testapi-%s" % TOKEN, "period": db.now_str()[:7],
            "billed_usd": 1.25, "note": "smoke invoice"}
    check("a client cannot file an invoice",
          a.post("/api/usage/reconciliation", json=body).status_code, 403)
    saved = owner.post("/api/usage/reconciliation", json=body)
    check("the operator files an invoice", saved.status_code, 201)
    row = saved.get_json()["reconciliation"]
    check("the computed cost and the variance are reported",
          "computed_usd" in row and "variance_usd" in row, True)
    listed = owner.get("/api/usage/reconciliation").get_json()["rows"]
    check("the month is in the list", any(item["id"] == row["id"] for item in listed), True)
    check("a client cannot read the list",
          a.get("/api/usage/reconciliation").status_code, 403)
    check("a bad month is refused",
          owner.post("/api/usage/reconciliation",
                     json=dict(body, period="soon")).status_code, 400)
    print("invoices are reconciled by the operator, and clients never see them")


def test_the_console_is_wired():
    """Static: the endpoints can be perfect and the screens still never load."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def read(*parts):
        return open(os.path.join(root, *parts), encoding="utf-8").read()

    admin_js = read("admin", "admin.js")
    page = read("admin", "index.html")
    css = read("admin", "admin.css")

    check("the workspace nav has Usage", "{ id: 'usage'" in admin_js, True)
    check("the platform nav has Usage", "#/platform/usage" in admin_js, True)
    check("both routes render a view",
          "viewUsage(" in admin_js and "viewPlatformUsage(" in admin_js, True)
    check("the client page reads its meter", "'/api/usage?days='" in admin_js, True)
    check("credits are added from the platform page",
          "'/api/orgs/' + org.id + '/credits'" in admin_js, True)
    check("the rate card is edited there", "'/api/usage/rates'" in admin_js, True)
    check("invoices are checked from the platform page",
          "'/api/usage/reconciliation'" in admin_js, True)
    voice_js = read("admin", "voice.js")
    check("the browser hands the voice token back when it stops",
          "'/api/voice/usage'" in voice_js and "data.session_id" in voice_js, True)
    check("the nav icon exists", 'id="i-credits"' in page, True)
    check("the balance is the page's one number", ".usage-balance" in css, True)
    print("the usage screens are wired to the endpoints")


def main():
    try:
        alpha, beta = _org("Alpha"), _org("Beta")
        test_a_client_sees_only_its_own_meter(alpha, beta)
        test_only_the_operator_moves_money(alpha, beta)
        test_the_rate_card_is_editable()
        test_the_monthly_grant_is_idempotent(alpha)
        test_a_voice_session_is_settled_by_its_owner(alpha, beta)
        test_invoices_are_reconciled_by_the_operator(alpha)
        test_the_console_is_wired()
    finally:
        cleanup()
    if FAILED:
        print("\n%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
        raise SystemExit(1)
    print("\nUSAGE API OK - own meter, operator's money, one grant per period")


if __name__ == "__main__":
    main()
