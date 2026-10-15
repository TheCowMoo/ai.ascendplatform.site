"""Settings tab: storage, resolution, masking and the API (stage S6).

Run: .\\venv\\Scripts\\python.exe tests\\smoke_settings.py

No API key is required and nothing is downloaded. The "Test" button is exercised
for real, but a missing key or no connectivity only changes its message - the test
asserts on the shape of the response, not on the network being reachable.

Everything the test stores is removed again afterwards, so the other suites (which
expect no Pexels key to be configured) are unaffected.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.pop("ALLOW_LIVE_ACTIONS", None)
os.environ.pop("PEXELS_API_KEY", None)
os.environ.pop("PEXELS_PER_PAGE", None)

import app as appmod  # noqa: E402
import settings  # noqa: E402
import tools.pexels as pexels  # noqa: E402

SECRET = "sk-smoke-settings-2f8a1c9e"
MEMBER_EMAIL = "settings-smoke-member@example.com"
MEMBER_PASSWORD = "member-pass-123"

TOUCHED = ("PEXELS_API_KEY", "PEXELS_PER_PAGE", "BRAND_NAME")


def login(email="admin@example.com", password="admin123"):
    client = appmod.app.test_client()
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return client


def find_field(body, key):
    for group in body["groups"]:
        for field in group["fields"]:
            if field["key"] == key:
                return field
    raise AssertionError("field %s missing from the payload" % key)


def panel_snapshot():
    """Only what is actually stored in the panel (no env vars, no SPEC defaults)."""
    return {key: settings.get(key) for key in settings.FIELDS
            if settings.source_of(key) == "panel"}


_saved = {}


def preserve(*keys):
    """Remember what is stored for these keys, so it can be put back afterwards.

    A test must never destroy configuration an operator has already saved, so every
    test that writes calls this first and restore() in its finally block.
    """
    for key in keys:
        if key not in _saved:
            _saved[key] = settings.get(key) if settings.source_of(key) == "panel" else None


def restore(*keys):
    """Put back whatever preserve() recorded (clearing where there was nothing)."""
    for key in keys:
        if key not in _saved:
            continue
        value = _saved[key]
        try:
            if value is None:
                settings.clear_value(key, "smoke-restore")
            else:
                settings.set_value(key, value, "smoke-restore")
        except settings.SettingsError:
            pass
    settings.forget()


def cleanup():
    """Leave the panel exactly as it was found."""
    restore(*TOUCHED)


def test_requires_admin(client):
    """Global config including secrets, so admin-only on every verb."""
    anonymous = appmod.app.test_client()
    assert anonymous.get("/api/settings").status_code == 401
    assert anonymous.put("/api/settings", json={"values": {}}).status_code == 401
    assert anonymous.post("/api/settings/test/pexels").status_code == 401
    print("anonymous  -> 401 on GET, PUT and test")

    # Ignore a duplicate: re-running must not fail, and login works either way.
    client.post("/api/users", json={
        "email": MEMBER_EMAIL, "name": "Smoke Member",
        "password": MEMBER_PASSWORD, "role": "member",
    })
    member = login(MEMBER_EMAIL, MEMBER_PASSWORD)
    assert member.get("/api/settings").status_code == 403
    assert member.put("/api/settings", json={"values": {"BRAND_NAME": "x"}}).status_code == 403
    print("member     -> 403 on GET and PUT")


def test_listing_masks_secrets(client):
    preserve("PEXELS_API_KEY")
    settings.set_value("PEXELS_API_KEY", SECRET, "smoke")
    try:
        resp = client.get("/api/settings")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert SECRET not in resp.get_data(as_text=True), "the API returned the secret"
        field = find_field(resp.get_json(), "PEXELS_API_KEY")
        assert field["secret"] is True and field["set"] is True, field
        assert field["source"] == "panel", field
        assert "value" not in field, field
        assert field["preview"] == settings.mask(SECRET), field
        print("listing    -> set=%s source=%s preview=%s (no value, no leak)"
              % (field["set"], field["source"], field["preview"]))
    finally:
        restore("PEXELS_API_KEY")

    body = client.get("/api/settings").get_json()
    print("groups     ->", [(g["id"], len(g["fields"])) for g in body["groups"]])


def test_saved_key_applies_without_restart(client):
    """The point of the panel: save it, and the tool uses it immediately."""
    preserve("PEXELS_API_KEY")
    settings.clear_value("PEXELS_API_KEY", "smoke-setup")
    try:
        assert pexels.api_key() == "", "expected a clean slate"
        resp = client.put("/api/settings", json={"values": {"PEXELS_API_KEY": SECRET}})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert "PEXELS_API_KEY" in resp.get_json()["changed"]
        assert pexels.api_key() == SECRET, "the tool did not pick up the saved key"
        print("PUT        -> pexels.api_key() reflects it with no restart")

        listed = find_field(resp.get_json(), "PEXELS_API_KEY")
        assert listed["source"] == "panel" and listed["set"] is True, listed

        settings.clear_value("PEXELS_API_KEY", "smoke")
        assert pexels.api_key() == "", "clearing did not fall back to the environment"
        print("cleared    -> falls back to the environment again")
    finally:
        restore("PEXELS_API_KEY")


def test_validation(client):
    """Bad input is refused, and nothing is written when it is."""
    cases = [
        ({"values": {"PATH": "/tmp/evil"}}, "unknown key"),
        ({"values": {"REDIS_URL": "redis://evil"}}, "structural key"),
        ({"values": {"PEXELS_PER_PAGE": "lots"}}, "not a number"),
        ({"values": {"PEXELS_PER_PAGE": "-4"}}, "negative number"),
        ({"values": {"SMTP_TLS": "maybe"}}, "not a boolean"),
    ]
    before = panel_snapshot()
    for payload, label in cases:
        resp = client.put("/api/settings", json=payload)
        assert resp.status_code == 400, (label, resp.status_code, resp.get_data(as_text=True))
        print("  reject (%s): %s" % (label, resp.get_json()["error"]))

    assert panel_snapshot() == before, "a rejected request still wrote something"
    print("           -> no invalid value was stored")


def test_blank_secret_keeps_the_stored_value(client):
    """The form never receives the secret, so an empty box means 'unchanged'."""
    preserve("PEXELS_API_KEY")
    settings.set_value("PEXELS_API_KEY", SECRET, "smoke")
    try:
        resp = client.put("/api/settings", json={"values": {"PEXELS_API_KEY": ""}})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert settings.get("PEXELS_API_KEY") == SECRET, "a blank save wiped the secret"
        print("blank      -> stored secret preserved")
    finally:
        restore("PEXELS_API_KEY")


def test_test_button(client):
    """POST /test/<group> always answers, and never with a traceback."""
    for group_id in ("pexels", "llm", "smtp"):
        resp = client.post("/api/settings/test/%s" % group_id)
        assert resp.status_code == 200, (group_id, resp.get_data(as_text=True))
        body = resp.get_json()
        assert isinstance(body.get("ok"), bool), body
        assert isinstance(body.get("output"), str), body
        print("  %-7s -> ok=%-5s %s" % (group_id, body["ok"], body["output"][:88]))

    unknown = client.post("/api/settings/test/nope")
    assert unknown.status_code == 200 and unknown.get_json()["ok"] is False
    print("  unknown -> ok=False (reported, not raised)")


def test_audit_trail():
    preserve("BRAND_NAME", "PEXELS_API_KEY")
    settings.set_value("BRAND_NAME", "Audited Brand", "smoke-actor")
    settings.set_value("PEXELS_API_KEY", SECRET, "smoke-actor")
    try:
        rows = settings.history(50)
        brand = [r for r in rows if r["key"] == "BRAND_NAME"]
        assert brand, "the change was not recorded"
        assert brand[0]["actor"] == "smoke-actor" and brand[0]["action"] == "set", brand[0]
        print("audit      -> %s %s by %s"
              % (brand[0]["action"], brand[0]["key"], brand[0]["actor"]))
        assert not any(SECRET in (r["detail"] or "") for r in rows), "secret in the audit trail"
        masked = [r for r in rows if r["key"] == "PEXELS_API_KEY"][0]
        print("audit      -> secret recorded only as a mask: %s" % masked["detail"])
    finally:
        restore("BRAND_NAME", "PEXELS_API_KEY")


def test_resolution_order():
    """panel beats environment beats SPEC default.

    Saves and restores any configured page size, so running the suite on a
    machine that already has one stored does not lose it.
    """
    preserve("PEXELS_PER_PAGE")
    settings.clear_value("PEXELS_PER_PAGE", "smoke-setup")
    os.environ["PEXELS_PER_PAGE"] = "9"
    try:
        settings.forget()
        assert pexels.page_size() == 9, pexels.page_size()
        print("env        -> page_size=%d" % pexels.page_size())
        settings.set_value("PEXELS_PER_PAGE", "3", "smoke")
        assert pexels.page_size() == 3, pexels.page_size()
        print("panel      -> page_size=%d (overrides the environment)" % pexels.page_size())
        settings.clear_value("PEXELS_PER_PAGE", "smoke")
        assert pexels.page_size() == 9, pexels.page_size()
        print("cleared    -> back to the environment")
    finally:
        os.environ.pop("PEXELS_PER_PAGE", None)
        restore("PEXELS_PER_PAGE")
    expected = int(_saved.get("PEXELS_PER_PAGE")
                   or settings.FIELDS["PEXELS_PER_PAGE"]["default"])
    assert pexels.page_size() == expected, (pexels.page_size(), expected)
    print("restored   -> page_size=%d" % pexels.page_size())


if __name__ == "__main__":
    admin = login()
    before = panel_snapshot()
    try:
        test_requires_admin(admin)
        test_listing_masks_secrets(admin)
        test_saved_key_applies_without_restart(admin)
        test_validation(admin)
        test_blank_secret_keeps_the_stored_value(admin)
        test_test_button(admin)
        test_audit_trail()
        test_resolution_order()
    finally:
        cleanup()
    # The suite must be non-destructive: whatever was configured before the run is
    # still configured after it.
    assert panel_snapshot() == before, ("panel not restored", before, panel_snapshot())
    print("panel      -> left exactly as found")
    print("SETTINGS OK")
