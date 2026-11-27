"""Sign-in and password-reset throttles, and the address they count.

Run: .\\venv\\Scripts\\python.exe tests\\test_login_guard.py

The login route must charge the same password-hash cost for an unknown address as
for a known one, and repeated failures must stop before they become a credential
stuffing loop.
"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="login-guard-"), "test.db").replace("\\", "/")
os.environ["SCHEDULER_ENABLED"] = "0"
os.environ.pop("ALLOW_LIVE_ACTIONS", None)

from werkzeug.security import generate_password_hash  # noqa: E402

import app as appmod  # noqa: E402
import db  # noqa: E402

db.init_db()

FAILED = []
PASSWORD = "correct-horse-9"
EMAIL = "guard@example.com"
IP = "198.51.100.32"


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


CLIENT = None


def make_user():
    global CLIENT
    if CLIENT is not None:
        return CLIENT
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO users (email, name, password_hash, role, org_id, status) "
            "VALUES (?, ?, ?, 'owner', NULL, 'active')",
            (EMAIL, "Guard", generate_password_hash(PASSWORD)))
    CLIENT = appmod.app.test_client()
    return CLIENT


def test_the_proxy_header_preferred_is_the_trusted_one():
    ctx = appmod.app.test_request_context(
        "/", headers={"X-Real-IP": "203.0.113.9",
                      "X-Forwarded-For": "1.2.3.4"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"})
    with ctx:
        check("X-Real-IP wins over a spoofed XFF", appmod._client_ip(), "203.0.113.9")

    ctx = appmod.app.test_request_context(
        "/", headers={"X-Forwarded-For": "1.2.3.4, 198.51.100.7"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"})
    with ctx:
        check("the last forwarded hop is used", appmod._client_ip(), "198.51.100.7")

    ctx = appmod.app.test_request_context(
        "/", headers={"X-Forwarded-For": "1.2.3.4"},
        environ_base={"REMOTE_ADDR": "203.0.113.5"})
    with ctx:
        check("a direct socket address beats XFF", appmod._client_ip(), "203.0.113.5")


def test_an_unknown_email_still_pays_the_hash_cost():
    client = appmod.app.test_client()
    seen = []
    original = appmod.check_password_hash
    appmod.check_password_hash = lambda stored, given: seen.append(stored) or False
    try:
        resp = client.post("/api/auth/login",
                           json={"email": "nobody@example.com", "password": "x"},
                           headers={"X-Real-IP": "198.51.100.31"})
    finally:
        appmod.check_password_hash = original
    check("an unknown account is a normal 401", resp.status_code, 401)
    check("the dummy hash was checked", bool(seen), True)
    check("and it is the same dummy hash",
          seen[0] if seen else None, appmod._dummy_password_hash())


def test_repeated_failures_are_throttled():
    client = make_user()
    appmod._LOGIN_FAILURES.clear()
    headers = {"X-Real-IP": IP}
    codes = []
    for _ in range(appmod.LOGIN_FAILURE_LIMIT):
        codes.append(client.post("/api/auth/login",
                                 json={"email": EMAIL, "password": "wrong"},
                                 headers=headers).status_code)
    check("the configured failures are ordinary 401s", set(codes), {401})
    resp = client.post("/api/auth/login",
                       json={"email": EMAIL, "password": "wrong"}, headers=headers)
    check("the next attempt is throttled", resp.status_code, 429)
    check("with a generic sentence", "Too many sign-in attempts" in resp.get_json()["error"], True)


def test_a_success_clears_the_pair_but_not_the_ip():
    client = make_user()
    appmod._LOGIN_FAILURES.clear()
    headers = {"X-Real-IP": "198.51.100.33"}
    for _ in range(3):
        client.post("/api/auth/login",
                    json={"email": EMAIL, "password": "wrong"}, headers=headers)
    ok = client.post("/api/auth/login",
                     json={"email": EMAIL, "password": PASSWORD}, headers=headers)
    check("the right password signs in", ok.status_code, 200)
    check("the pair's failures are gone",
          ("pair", "198.51.100.33", EMAIL) in appmod._LOGIN_FAILURES, False)
    for _ in range(appmod.LOGIN_FAILURE_LIMIT):
        client.post("/api/auth/login",
                    json={"email": EMAIL, "password": "wrong"}, headers=headers)
    blocked = client.post("/api/auth/login",
                          json={"email": EMAIL, "password": PASSWORD}, headers=headers)
    check("but a fresh run of failures is throttled again", blocked.status_code, 429)


def test_forgot_password_is_limited_per_address():
    appmod._RATE_HITS.clear()
    client = appmod.app.test_client()
    headers = {"X-Real-IP": "198.51.100.40"}
    codes = []
    for _ in range(5):
        codes.append(client.post("/api/auth/forgot-password",
                                 json={"email": "nobody@example.com"},
                                 headers=headers).status_code)
    check("the configured requests are accepted", set(codes), {200})
    resp = client.post("/api/auth/forgot-password",
                       json={"email": "nobody@example.com"}, headers=headers)
    check("the next one is throttled", resp.status_code, 429)


if __name__ == "__main__":
    test_the_proxy_header_preferred_is_the_trusted_one()
    test_an_unknown_email_still_pays_the_hash_cost()
    test_repeated_failures_are_throttled()
    test_a_success_clears_the_pair_but_not_the_ip()
    test_forgot_password_is_limited_per_address()
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nLOGIN GUARD OK - one hash cost, bounded failures, trusted address")
