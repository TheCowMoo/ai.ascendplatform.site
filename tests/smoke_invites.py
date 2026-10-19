"""Invitations: a single-use link that creates the account before it is accepted.

Run: .\\venv\\Scripts\\python.exe tests\\smoke_invites.py

The interesting properties are security ones - the token is stored hashed, it works
once, it expires, and resending kills the previous link. Also that a *failed email*
still leaves a usable invitation, because the account and the token are created before
the send is attempted.
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.security import check_password_hash  # noqa: E402

import app as appmod  # noqa: E402
import db  # noqa: E402
import invites as invites_service  # noqa: E402

TOKEN = uuid.uuid4().hex[:8]


def _make_invite(role="client_member", org_id=None):
    email = "invitee-%s-%s@example.test" % (TOKEN, uuid.uuid4().hex[:6])
    invite, token = invites_service.create(email, name="Invitee", role=role, org_id=org_id)
    return invite, token, email


def _owner():
    client = appmod.app.test_client()
    resp = client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "admin123"}
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return client


def test_the_account_is_locked_until_accepted():
    db.init_db()
    invite, token, email = _make_invite()
    assert invite["status"] == "pending", invite
    assert invite["email"] == email, invite

    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT token_hash FROM invites WHERE id = ?", (invite["id"],)
        ).fetchone()
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

    # The token is stored as a hash: the plaintext must not be recoverable from the row.
    assert row["token_hash"] != token, "the token was stored in plaintext"
    # The account exists, but nobody can sign in to it yet.
    assert user is not None, "the invited account was not created"
    assert check_password_hash(user["password_hash"], token) is False
    assert check_password_hash(user["password_hash"], "") is False
    print("invite #%s created; account locked, token hashed" % invite["id"])


def test_accept_sets_the_password_and_spends_the_token():
    invite, token, email = _make_invite()
    user = invites_service.accept(token, "brand-new-pass-1")
    assert user["email"] == email, user
    assert invites_service.lookup(token)["status"] == "accepted"

    client = appmod.app.test_client()
    login = client.post(
        "/api/auth/login", json={"email": email, "password": "brand-new-pass-1"}
    )
    assert login.status_code == 200, login.get_data(as_text=True)

    try:
        invites_service.accept(token, "another-pass-1")
    except invites_service.InviteError as exc:
        assert "already been used" in str(exc), exc
    else:
        raise AssertionError("a spent token must not work twice")
    print("accepted -> signed in as", email)


def test_revoked_and_expired_links_are_refused():
    revoked, token, _ = _make_invite()
    invites_service.revoke(revoked["id"])
    try:
        invites_service.accept(token, "some-password-1")
    except invites_service.InviteError as exc:
        assert "withdrawn" in str(exc), exc

    expired, expired_token, _ = _make_invite()
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE invites SET expires_at = ? WHERE id = ?",
            ("2000-01-01 00:00:00", expired["id"]),
        )
    assert invites_service.get_invite(expired["id"])["status"] == "expired"
    try:
        invites_service.accept(expired_token, "some-password-1")
    except invites_service.InviteError as exc:
        assert "expired" in str(exc), exc

    try:
        invites_service.accept("not-a-real-token", "some-password-1")
    except invites_service.InviteError as exc:
        assert "not valid" in str(exc), exc
    print("revoked, expired and unknown tokens all refused")


def test_resend_retires_the_previous_link():
    invite, token, _ = _make_invite()
    refreshed, fresh = invites_service.resend(invite["id"])
    assert len(fresh) > 20 and fresh != token
    assert invites_service.lookup(token) is None, "the old link still works after a resend"
    assert invites_service.lookup(fresh)["status"] == "pending"
    print("resend rotated the token for invite #%s" % invite["id"])


def test_a_failed_send_still_leaves_a_usable_invitation():
    """GHL is not configured in tests, so creating via the API must still succeed."""
    client = _owner()
    email = "api-%s-%s@example.test" % (TOKEN, uuid.uuid4().hex[:6])
    resp = client.post("/api/invites", json={"email": email, "role": "client_admin"})
    assert resp.status_code == 502, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["invite"]["email"] == email, body
    assert "/#/accept/" in body["link"], body
    assert "not configured" in body["error"].lower(), body
    assert invites_service.get_invite(body["invite"]["id"])["status"] == "pending"
    print("send failed as expected, invitation #%s still usable" % body["invite"]["id"])


def test_the_accept_route_sets_the_password_and_signs_in():
    invite, token, email = _make_invite(role="client_admin")
    client = appmod.app.test_client()

    short = client.post("/api/invites/accept", json={"token": token, "password": "short"})
    assert short.status_code == 400, short.get_data(as_text=True)
    assert "at least" in short.get_json()["error"], short.get_json()

    accepted = client.post(
        "/api/invites/accept", json={"token": token, "password": "open-sesame-1"}
    )
    assert accepted.status_code == 200, accepted.get_data(as_text=True)
    me = client.get("/api/auth/me").get_json()["user"]
    assert me["email"] == email, me
    print("accept route signed in", me["email"])


def test_the_preview_is_public_and_says_nothing_secret():
    invite, token, email = _make_invite()
    anon = appmod.app.test_client()
    resp = anon.get("/api/invites/%s" % token)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()["invite"]
    assert body["email"] == email, body
    assert body["status"] == "pending", body
    assert "token" not in body, body
    assert anon.get("/api/invites/nope-not-a-token").status_code == 404
    print("public preview ->", body["email"], body["status"])


def test_invites_are_operator_only():
    anon = appmod.app.test_client()
    assert anon.get("/api/invites").status_code in (302, 401, 403)
    print("invite list is not public")


def test_the_template_carries_every_merge_value():
    """The built-in body must use every token, and render must leave none behind."""
    body = invites_service.DEFAULT_BODY
    for key, _label, _sample in invites_service.TOKENS:
        assert "{{%s}}" % key in body, "the built-in template is missing {{%s}}" % key

    invite, token, _ = _make_invite()
    _subject, html = invites_service.render(invite, token, base_url="https://example.test")
    for key, _label, _sample in invites_service.TOKENS:
        assert "{{%s}}" % key not in html, "render left {{%s}} unreplaced" % key
    assert "https://example.test/admin/#/accept/" in html, html[:200]
    print("template uses all %d merge values and render fills them" % len(invites_service.TOKENS))


def test_the_api_offers_the_template_and_its_values():
    client = _owner()
    data = client.get("/api/platform/email").get_json()
    keys = {token["key"] for token in data["tokens"]}
    assert keys == {key for key, _label, _sample in invites_service.TOKENS}, keys
    assert "{{link}}" in data["template"]["body"]
    assert "{{link}}" in data["template"]["default_body"]
    print("API template offers", len(keys), "merge values")


def main():
    test_the_account_is_locked_until_accepted()
    test_accept_sets_the_password_and_spends_the_token()
    test_revoked_and_expired_links_are_refused()
    test_resend_retires_the_previous_link()
    test_a_failed_send_still_leaves_a_usable_invitation()
    test_the_accept_route_sets_the_password_and_signs_in()
    test_the_preview_is_public_and_says_nothing_secret()
    test_the_template_carries_every_merge_value()
    test_the_api_offers_the_template_and_its_values()
    test_invites_are_operator_only()
    print("\nINVITES OK")


if __name__ == "__main__":
    main()

