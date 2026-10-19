"""The platform's own mail goes through GoHighLevel, not the clients' SMTP.

Run: .\\venv\\Scripts\\python.exe tests\\smoke_platform_mail.py

Offline: ``_transport`` is replaced, so this proves the request shapes (contact upsert,
then conversation message), the custom-field mapping and the error paths without an
account or a network. GHL cannot send to an address - only to a contact - so the pair
of calls is the part worth pinning down.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402
import platform_mail  # noqa: E402
import settings  # noqa: E402

CALLS = []


def _respond(method, url, headers, payload=None, timeout=20):
    CALLS.append({"method": method, "url": url, "headers": headers, "payload": payload})
    if url.endswith("/contacts/upsert"):
        return 200, json.dumps({"contact": {"id": "contact-123"}})
    if url.endswith("/conversations/messages"):
        return 200, json.dumps(
            {"messageId": "msg-1", "emailMessageId": "eml-1", "conversationId": "conv-1"}
        )
    if "/customFields" in url:
        return 200, json.dumps(
            {
                "customFields": [
                    {"id": "cf-source", "name": "Source", "fieldKey": "contact.source",
                     "dataType": "TEXT"},
                    {"id": "cf-plan", "name": "Plan", "fieldKey": "contact.plan",
                     "dataType": "SINGLE_OPTIONS", "picklistOptions": ["starter", "growth"]},
                ]
            }
        )
    if "/locations/" in url:
        return 200, json.dumps({"location": {"id": "loc-1", "name": "Ascend HQ"}})
    return 404, json.dumps({"message": "not found: %s" % url})


def _unauthorised(method, url, headers, payload=None, timeout=20):
    return 401, json.dumps({"message": "Invalid JWT"})


def _configure():
    os.environ["GHL_API_TOKEN"] = "pit-test-token"
    os.environ["GHL_LOCATION_ID"] = "loc-1"
    os.environ.pop("GHL_API_BASE", None)
    os.environ.pop("GHL_FROM_EMAIL", None)
    settings.forget()
    platform_mail._transport = _respond


def _unconfigure():
    for key in ("GHL_API_TOKEN", "GHL_LOCATION_ID"):
        os.environ.pop(key, None)
    settings.forget()


def _call_to(suffix):
    matches = [call for call in CALLS if call["url"].endswith(suffix)]
    assert matches, "no call to %s in %s" % (suffix, [c["url"] for c in CALLS])
    return matches[-1]


def test_unconfigured_refuses_without_a_request():
    db.init_db()
    _unconfigure()
    CALLS.clear()
    try:
        platform_mail.send("someone@example.test", "Hi", "<p>Hello</p>")
    except platform_mail.MailError as exc:
        assert "not configured" in str(exc).lower(), exc
        print("unconfigured ->", exc)
    else:
        raise AssertionError("an unconfigured mailer must refuse")
    assert CALLS == [], CALLS


def test_send_upserts_the_contact_then_messages_it():
    _configure()
    sender = platform_mail.save_sender(
        {
            "name": "Ascend",
            "from_name": "Ascend",
            "from_email": "hello@ascend.test",
            "reply_to": "reply@ascend.test",
            "custom_fields": {"Source": "ascendplatform", "contact.plan": "growth",
                              "NotAField": "ignored"},
            "is_default": True,
        }
    )
    CALLS.clear()
    result = platform_mail.send("invitee@example.test", "Your invite", "<p>Hello there</p>")
    assert result["contact_id"] == "contact-123", result

    upsert = _call_to("/contacts/upsert")
    body = upsert["payload"]
    assert body["locationId"] == "loc-1", body
    assert body["email"] == "invitee@example.test", body
    # Keys resolve by name or fieldKey; the unknown one is dropped rather than sent.
    assert body["customFields"] == [
        {"id": "cf-source", "value": "ascendplatform"},
        {"id": "cf-plan", "value": "growth"},
    ], body["customFields"]
    assert upsert["headers"]["Authorization"] == "Bearer pit-test-token", upsert["headers"]
    assert upsert["headers"]["Version"] == "2021-07-28", upsert["headers"]

    message = _call_to("/conversations/messages")["payload"]
    assert message["type"] == "Email", message
    assert message["contactId"] == "contact-123", message
    assert message["subject"] == "Your invite", message
    assert "<p>Hello there</p>" in message["html"], message
    assert message["emailFrom"] == "Ascend <hello@ascend.test>", message
    assert message["emailReplyTo"] == "reply@ascend.test", message
    print("send -> contact %s, message %s" % (result["contact_id"], result["message_id"]))

    platform_mail.delete_sender(sender["id"])


def test_test_connection_reports_the_location_and_fields():
    _configure()
    result = platform_mail.test_connection()
    print("test connection ->", result)
    assert result["ok"] is True, result
    assert "Ascend HQ" in result["output"], result
    assert "2 contact custom field(s)" in result["output"], result


def test_a_rejected_token_is_reported_not_raised():
    _configure()
    platform_mail._transport = _unauthorised
    try:
        result = platform_mail.test_connection()
    finally:
        platform_mail._transport = _respond
    assert result["ok"] is False, result
    assert "401" in result["output"], result
    print("unauthorised ->", result["output"][:80])


def test_only_one_sender_is_the_default():
    _configure()
    first = platform_mail.save_sender({"name": "One", "from_email": "one@ascend.test"})
    second = platform_mail.save_sender(
        {"name": "Two", "from_email": "two@ascend.test", "is_default": True}
    )
    try:
        defaults = [s["id"] for s in platform_mail.senders() if s["is_default"]]
        assert defaults == [second["id"]], defaults
        assert platform_mail.default_sender()["id"] == second["id"]
        print("default sender ->", platform_mail.default_sender()["from_email"])
    finally:
        platform_mail.delete_sender(first["id"])
        platform_mail.delete_sender(second["id"])


def test_a_client_cannot_see_or_use_the_platform_sender():
    _configure()
    sender = platform_mail.save_sender(
        {"name": "Platform", "from_email": "hello@ascend.test", "is_default": True}
    )
    try:
        assert platform_mail.senders(org_id=987654) == [], "platform sender leaked into a client"
        assert sender["org_id"] is None, sender
        print("platform sender is invisible to a client scope")
    finally:
        platform_mail.delete_sender(sender["id"])


def test_the_request_identifies_itself():
    """Cloudflare blocks urllib's default User-Agent with 403 error 1010."""
    _configure()
    CALLS.clear()
    sender = platform_mail.save_sender(
        {"name": "UA", "from_email": "ua@ascend.test", "is_default": True}
    )
    try:
        platform_mail.send("ua-check@example.test", "Hi", "<p>x</p>")
        agent = _call_to("/contacts/upsert")["headers"].get("User-Agent", "")
        assert agent and "urllib" not in agent.lower(), agent
        assert agent.startswith("AscendPlatform"), agent
        print("User-Agent ->", agent)
    finally:
        platform_mail.delete_sender(sender["id"])


def test_a_cloudflare_block_is_explained_as_such():
    """A 1010 must not read like a bad token."""
    _configure()

    def blocked(method, url, headers, payload=None, timeout=20):
        return 403, json.dumps({
            "title": "Error 1010: Access denied",
            "detail": "The site owner has blocked access based on your browser's signature.",
            "error_code": 1010,
            "error_name": "browser_signature_banned",
        })

    platform_mail._transport = blocked
    try:
        result = platform_mail.test_connection()
    finally:
        platform_mail._transport = _respond
    assert result["ok"] is False, result
    assert "Cloudflare" in result["output"], result
    assert "1010" in result["output"], result
    print("cloudflare ->", result["output"][:90])


def main():
    test_unconfigured_refuses_without_a_request()
    test_send_upserts_the_contact_then_messages_it()
    test_test_connection_reports_the_location_and_fields()
    test_a_rejected_token_is_reported_not_raised()
    test_only_one_sender_is_the_default()
    test_a_client_cannot_see_or_use_the_platform_sender()
    test_the_request_identifies_itself()
    test_a_cloudflare_block_is_explained_as_such()
    _unconfigure()
    print("\nPLATFORM MAIL OK")


if __name__ == "__main__":
    main()
