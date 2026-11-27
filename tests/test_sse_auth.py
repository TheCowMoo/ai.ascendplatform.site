"""The SSE stream must use the live user row, not the cookie's stale copy.

Run: .\\venv\\Scripts\\python.exe tests\\test_sse_auth.py

A suspended or deleted account used to keep its stream because the FastAPI
dependency only checked the signed cookie and the org's status. A user with no org
could also read NULL-org jobs, because ``None != None`` was false.
"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="sse-auth-"), "test.db").replace("\\", "/")
os.environ["SCHEDULER_ENABLED"] = "0"
os.environ.pop("JOB_BACKEND", None)
os.environ.pop("REDIS_URL", None)

from fastapi.testclient import TestClient  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

import app as appmod  # noqa: E402
import asgi  # noqa: E402
import db  # noqa: E402
import jobs  # noqa: E402
import orgs as orgs_service  # noqa: E402

db.init_db()

FAILED = []
PASSWORD = "sse-pass-123"


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


def make_user(email, role, org_id):
    with db.get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO users (email, name, password_hash, role, org_id, status) "
            "VALUES (?, ?, ?, ?, ?, 'active')",
            (email, email.split("@")[0], generate_password_hash(PASSWORD), role, org_id))
        return cur.lastrowid


def login_cookie(email):
    client = appmod.app.test_client()
    resp = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    header = resp.headers.get("Set-Cookie") or ""
    assert "session=" in header, header
    value = header.split("session=", 1)[1].split(";", 1)[0].strip().strip('"')
    assert value, header
    return value


def fast_client(cookie):
    client = TestClient(asgi.app)
    # No explicit domain: httpx then sends it to the TestClient's ``testserver``
    # host. A domain of "testserver" is treated as a different host and is not sent.
    client.cookies.set("session", value=cookie, path="/")
    return client


def test_a_suspended_or_deleted_user_cannot_stream():
    org = orgs_service.create_org({"name": "SSE Client"})["id"]
    uid = make_user("sse-admin@example.com", "client_admin", org)
    job_id = jobs.create_job("SSE", "x", uid, "selector", "echo", org_id=org)
    client = fast_client(login_cookie("sse-admin@example.com"))

    with db.get_connection() as conn:
        conn.execute("UPDATE users SET status = 'suspended' WHERE id = ?", (uid,))
    check("a suspended account is refused",
          client.get("/api/jobs/%d/stream" % job_id).status_code, 403)

    with db.get_connection() as conn:
        conn.execute("UPDATE users SET status = 'active' WHERE id = ?", (uid,))
        conn.execute("DELETE FROM users WHERE id = ?", (uid,))
    check("a deleted account is refused",
          client.get("/api/jobs/%d/stream" % job_id).status_code, 401)
    print("the stream reloads the account on every request")


def test_a_user_with_no_org_cannot_read_a_null_org_job():
    uid = make_user("sse-noorg@example.com", "member", None)
    job_id = jobs.create_job("Platform job", "x", None, "selector", "echo", org_id=None)
    client = fast_client(login_cookie("sse-noorg@example.com"))
    check("no org means no NULL-org job",
          client.get("/api/jobs/%d/stream" % job_id).status_code, 404)
    print("a user with no tenant is confined to nothing")


def test_a_demoted_owner_loses_the_owner_surface():
    uid = make_user("sse-owner@example.com", "owner", None)
    client = fast_client(login_cookie("sse-owner@example.com"))
    check("the owner can read the provider list",
          client.get("/api/providers").status_code, 200)
    org = orgs_service.create_org({"name": "Demotion Client"})["id"]
    with db.get_connection() as conn:
        conn.execute("UPDATE users SET role = 'client_admin', org_id = ? WHERE id = ?",
                     (org, uid))
    check("a demoted owner cannot",
          client.get("/api/providers").status_code, 403)
    with db.get_connection() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (uid,))
    check("and a deleted owner is signed out",
          client.get("/api/providers").status_code, 401)
    print("owner checks use the reloaded role, not the cookie")


def test_the_legacy_admin_spelling_is_still_an_owner():
    uid = make_user("sse-legacy@example.com", "admin", None)
    client = fast_client(login_cookie("sse-legacy@example.com"))
    check("legacy admin is an owner",
          client.get("/api/providers").status_code, 200)
    print("the legacy admin role still reaches the owner surface")


if __name__ == "__main__":
    test_a_suspended_or_deleted_user_cannot_stream()
    test_a_user_with_no_org_cannot_read_a_null_org_job()
    test_a_demoted_owner_loses_the_owner_surface()
    test_the_legacy_admin_spelling_is_still_an_owner()
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nSSE AUTH OK - live status, live role, no NULL-org leak")
