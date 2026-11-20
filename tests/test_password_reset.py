"""Forgot-password links: private, expiring, single-use and audited.

Run: .\\venv\\Scripts\\python.exe tests\\test_password_reset.py

The email is captured instead of sent. The token is still treated as a secret: these
tests hold the plaintext only long enough to exercise the public reset endpoint.
"""

import os
import re
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("NOTIFICATIONS_ENABLED", "0")

import app as appmod  # noqa: E402
import db  # noqa: E402
import orgs as orgs_service  # noqa: E402
import password_resets  # noqa: E402
import platform_mail  # noqa: E402
import user_service  # noqa: E402

TAG = uuid.uuid4().hex[:8]
ORG_NAME = "Reset Test %s" % TAG
EMAIL = "reset-%s@example.test" % TAG
PASSWORD = "reset-old-pass-1234"
STATE = {}


def login(email, password):
    client = appmod.app.test_client()
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    return client, response


def cleanup():
    with db.get_connection() as conn:
        org = conn.execute("SELECT id FROM orgs WHERE name = ?", (ORG_NAME,)).fetchone()
        if org:
            conn.execute("DELETE FROM password_resets WHERE user_id IN "
                         "(SELECT id FROM users WHERE org_id = ?)", (org["id"],))
            conn.execute("DELETE FROM user_events WHERE org_id = ?", (org["id"],))
            conn.execute("DELETE FROM users WHERE org_id = ?", (org["id"],))
            conn.execute("DELETE FROM orgs WHERE id = ?", (org["id"],))
        conn.execute("DELETE FROM password_resets WHERE email = ?", (EMAIL,))
        conn.execute("DELETE FROM users WHERE email = ?", (EMAIL,))


def setup():
    cleanup()
    db.init_db()
    org = orgs_service.create_org({
        "name": ORG_NAME, "email": EMAIL, "password": PASSWORD,
    })
    assert org, "could not create the reset fixture client"
    STATE["org"] = org
    with db.get_connection() as conn:
        STATE["user_id"] = conn.execute(
            "SELECT id FROM users WHERE email = ?", (EMAIL,)
        ).fetchone()["id"]


def test_forgot_password_hides_whether_an_account_exists():
    original = platform_mail.send
    platform_mail.send = lambda *args, **kwargs: {"ok": True}
    try:
        anon = appmod.app.test_client()
        unknown = anon.post("/api/auth/forgot-password",
                            json={"email": "nobody-%s@example.test" % TAG})
        known = anon.post("/api/auth/forgot-password", json={"email": EMAIL})
    finally:
        platform_mail.send = original
    assert unknown.status_code == 200 and known.status_code == 200
    assert unknown.get_json() == known.get_json(), (unknown.get_json(), known.get_json())
    assert "If that email is registered" in known.get_json()["message"]
    print("forgot endpoint gives the same answer for known and unknown emails")


def test_the_token_is_hashed_and_single_use():
    # The previous test requested a link; move it outside the anti-spam throttle.
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE password_resets SET requested_at = '2000-01-01 00:00:00' WHERE email = ?",
            (EMAIL,),
        )
    sent = []
    original = platform_mail.send

    def capture(to, subject, html, **kwargs):
        sent.append({"to": to, "subject": subject, "html": html, "kwargs": kwargs})
        return {"ok": True}

    platform_mail.send = capture
    try:
        client = appmod.app.test_client()
        response = client.post("/api/auth/forgot-password", json={"email": EMAIL})
        assert response.status_code == 200
        assert len(sent) == 1, sent
        match = re.search(r"/admin/#/reset/([A-Za-z0-9_-]+)", sent[0]["html"])
        assert match, sent[0]["html"][:500]
        token = match.group(1)
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT token_hash FROM password_resets WHERE email = ? ORDER BY id DESC",
                (EMAIL,),
            ).fetchone()
        assert row and row["token_hash"] != token, "the reset token was stored in plaintext"

        preview = appmod.app.test_client().get("/api/auth/reset/" + token)
        assert preview.status_code == 200, preview.get_data(as_text=True)
        assert preview.get_json()["reset"]["email"] == EMAIL

        mismatch = appmod.app.test_client().post("/api/auth/reset", json={
            "token": token, "password": "reset-new-pass-1234",
            "confirm_password": "different-pass-1234",
        })
        assert mismatch.status_code == 400, mismatch.get_data(as_text=True)

        reset = appmod.app.test_client().post("/api/auth/reset", json={
            "token": token, "password": "reset-new-pass-1234",
            "confirm_password": "reset-new-pass-1234",
        })
        assert reset.status_code == 200, reset.get_data(as_text=True)

        signed_in, response = login(EMAIL, "reset-new-pass-1234")
        assert response.status_code == 200, response.get_data(as_text=True)
        assert signed_in.get("/api/auth/me").get_json()["user"]["email"] == EMAIL
        assert login(EMAIL, PASSWORD)[1].status_code == 401
        assert appmod.app.test_client().post("/api/auth/reset", json={
            "token": token, "password": "another-pass-1234",
        }).status_code == 400
        used = appmod.app.test_client().get("/api/auth/reset/" + token).get_json()["reset"]
        assert used["status"] == "used", used
        print("token is hashed, single-use, and the old password no longer works")
    finally:
        platform_mail.send = original


def test_expired_tokens_are_refused_and_other_links_are_revoked():
    # Move the last request out of the throttle window before asking for a fresh one.
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE password_resets SET requested_at = '2000-01-01 00:00:00' WHERE email = ?",
            (EMAIL,),
        )
    first, _user = password_resets.request(EMAIL)
    assert first
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE password_resets SET requested_at = '2000-01-01 00:00:00' WHERE email = ?",
            (EMAIL,),
        )
    second, _user = password_resets.request(EMAIL)
    assert second
    assert password_resets.get(first)["status"] == "used"
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE password_resets SET expires_at = '2000-01-01 00:00:00' WHERE email = ?",
            (EMAIL,),
        )
    expired = appmod.app.test_client().post("/api/auth/reset", json={
        "token": second, "password": "another-pass-1234",
    })
    assert expired.status_code == 400, expired.get_data(as_text=True)
    print("expired links and superseded links are refused")


def test_a_password_change_revokes_reset_links():
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE password_resets SET requested_at = '2000-01-01 00:00:00' WHERE email = ?",
            (EMAIL,),
        )
    token, _user = password_resets.request(EMAIL)
    assert token
    user_service.reset_password(
        STATE["user_id"], "admin-set-pass-1234", actor_id=None,
        actor_owner=True, actor_org=STATE["org"]["id"],
    )
    assert password_resets.get(token)["status"] == "used"
    print("an administrator reset revokes an outstanding email link")


def test_the_reset_is_audited_and_the_ui_has_the_expected_controls():
    with db.get_connection() as conn:
        events = conn.execute(
            "SELECT action FROM user_events WHERE target_id = ? ORDER BY id",
            (STATE["user_id"],),
        ).fetchall()
    assert any(row["action"] == "password_reset" for row in events), events

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "admin", "admin.js"), encoding="utf-8") as handle:
        script = handle.read()
    assert "Forgot password?" in script
    assert "#/forgot-password" in script
    assert "function viewResetPassword" in script
    assert "Confirm password" in script
    assert "confirm_password" in script
    assert "The two passwords do not match" in script
    with open(os.path.join(root, "password_resets.py"), encoding="utf-8") as handle:
        reset_service = handle.read()
    assert "#/reset/" in reset_service
    print("reset audit and forgot/reset UI controls are present")


def main():
    try:
        setup()
        test_forgot_password_hides_whether_an_account_exists()
        test_the_token_is_hashed_and_single_use()
        test_expired_tokens_are_refused_and_other_links_are_revoked()
        test_a_password_change_revokes_reset_links()
        test_the_reset_is_audited_and_the_ui_has_the_expected_controls()
        print("\nPASSWORD RESET OK")
    finally:
        cleanup()


if __name__ == "__main__":
    main()
