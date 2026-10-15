"""
Meta (Messenger / Instagram / WhatsApp) webhook integration.

Used by the unified ``/webhooks/meta`` endpoint in ``app.py``. This module:

  * verifies ``X-Hub-Signature-256`` payloads (HMAC-SHA256 with the app secret),
  * normalises the three inbound payload shapes into a common list of events,
  * sends replies back through the Graph API (Messenger/Instagram Send API and
    the WhatsApp Cloud API).

Outbound HTTP uses ``urllib`` so no extra dependency is required — matching the
convention already used in ``app.py``.
"""

import json
import hmac
import hashlib
import urllib.parse
import urllib.request

import settings


# Read per call, so a version change in the Settings tab applies without a restart.
def graph_api_version():
    return settings.get("GRAPH_API_VERSION") or "v21.0"


def graph_api_base():
    return "https://graph.facebook.com/" + graph_api_version()

# Messenger/Instagram "objects" that deliver DMs.
MESSAGING_OBJECTS = {"page": "messenger", "instagram": "instagram"}


def verify_signature(raw_body, header, app_secret):
    """Validate Meta's ``X-Hub-Signature-256`` header against the raw request body.

    Returns False when the secret is missing or the signature doesn't match.
    Comparison is constant-time to avoid timing attacks.
    """
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    provided = header.split("=", 1)[1].strip()
    return hmac.compare_digest(expected, provided)


def _post_json(url, payload, access_token=""):
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if access_token:
        headers["Authorization"] = "Bearer " + access_token
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status, resp.read().decode("utf-8", "replace")


def send_messenger(access_token, recipient_id, text):
    """Send a text DM via the Messenger/Instagram Send API (``/me/messages``)."""
    url = graph_api_base() + "/me/messages"
    payload = {"recipient": {"id": recipient_id}, "messaging_type": "RESPONSE", "message": {"text": text}}
    return _post_json(url, payload, access_token)


def send_whatsapp(phone_number_id, access_token, recipient_id, text):
    """Send a WhatsApp text message via the Cloud API."""
    url = graph_api_base() + "/" + urllib.parse.quote(str(phone_number_id), safe="") + "/messages"
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient_id,
        "type": "text",
        "text": {"body": text},
    }
    return _post_json(url, payload, access_token)


def send_reply(channel, recipient_id, text):
    """Dispatch a reply to the right Graph API endpoint based on the channel row."""
    platform = channel.get("platform")
    token = channel.get("access_token") or ""
    if platform in ("messenger", "instagram"):
        return send_messenger(token, recipient_id, text)
    if platform == "whatsapp":
        return send_whatsapp(channel.get("external_id"), token, recipient_id, text)
    raise ValueError("Unsupported platform: %r" % (platform,))


def _text_from_message(message):
    """Return trimmed text from a Messenger/Instagram message dict, else None.

    Ignores echoes (the Page's own outgoing messages) and non-text messages.
    """
    if not isinstance(message, dict) or message.get("is_echo"):
        return None
    text_obj = message.get("text")
    if isinstance(text_obj, str):
        body = text_obj.strip()
    elif isinstance(text_obj, dict):
        body = (text_obj.get("body") or "").strip()
    else:
        return None
    return body or None


def _parse_messaging(entry, platform, events):
    external_id = str(entry.get("id") or "")
    # Standard shape: entry.messaging[] (Messenger and classic Instagram).
    for event in entry.get("messaging", []) or []:
        message = event.get("message") or {}
        text = _text_from_message(message)
        sender = (event.get("sender") or {}).get("id")
        if not text or not sender:
            continue
        events.append({
            "platform": platform,
            "external_id": external_id,
            "sender_id": str(sender),
            "mid": message.get("mid") or "",
            "text": text,
        })
    # Newer Instagram shape: entry.changes[].value with field "messages".
    for change in entry.get("changes", []) or []:
        if change.get("field") != "messages":
            continue
        value = change.get("value") or {}
        message = value.get("message") or {}
        text = _text_from_message(message)
        sender = (value.get("sender") or {}).get("id")
        if not text or not sender:
            continue
        events.append({
            "platform": platform,
            "external_id": external_id,
            "sender_id": str(sender),
            "mid": message.get("mid") or "",
            "text": text,
        })


def _parse_whatsapp(entry, events):
    for change in entry.get("changes", []) or []:
        if change.get("field") != "messages":
            continue
        value = change.get("value") or {}
        metadata = value.get("metadata") or {}
        external_id = str(metadata.get("phone_number_id") or "")
        for message in value.get("messages", []) or []:
            if message.get("type") != "text":
                continue
            text = ((message.get("text") or {}).get("body") or "").strip()
            sender = message.get("from")
            if not text or not sender:
                continue
            events.append({
                "platform": "whatsapp",
                "external_id": external_id,
                "sender_id": str(sender),
                "mid": message.get("id") or "",
                "text": text,
            })


def parse_events(payload):
    """Normalise a Meta webhook payload into a list of inbound message dicts.

    Each dict has: ``platform``, ``external_id``, ``sender_id``, ``mid``, ``text``.
    Unknown objects and non-text events are silently ignored.
    """
    events = []
    payload = payload or {}
    obj = payload.get("object")
    for entry in payload.get("entry", []) or []:
        if obj in MESSAGING_OBJECTS:
            _parse_messaging(entry, MESSAGING_OBJECTS[obj], events)
        elif obj == "whatsapp_business_account":
            _parse_whatsapp(entry, events)
    return events
