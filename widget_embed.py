"""Where the public chat widget is inserted into a client's own pages.

The site row has carried ``linked_bot_id`` for a while, but nothing ever read it:
provisioning wrote the page and publishing wrote the page, both without the widget.
This module is the one place that turns that stored id into the script tag, so the
provisioner, the publisher and the Operator's index writer cannot disagree.

It is deliberately pure except for the optional ownership check in
:func:`inject_for_site`. The bot id is validated and HTML-escaped, and the script
is only ever inserted before ``</body>`` (or appended when a fragment has no body).
"""

import html
import re

BOT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")
_BODY_END = re.compile(r"</body\s*>", re.I)
# Our own embedding script, including a previous bot id. A site that changes
# assistants must not end up with two launchers on the page.
_WIDGET_SCRIPT = re.compile(
    r"<script\b[^>]*\bdata-bot\s*=\s*(['\"])[^'\"]+\1[^>]*>\s*</script>",
    re.I,
)


def valid_bot_id(value):
    return bool(BOT_ID_RE.match(str(value or "").strip()))


def platform_origin():
    """The public origin the widget script and chat API are served from.

    ``PLATFORM_URL`` is the explicit setting. ``ADMIN_DOMAIN`` is the fallback the
    rest of the app uses. A localhost value is not usable in a page served to a
    visitor, so it is treated as unset rather than producing a dead script tag.
    """
    try:
        import settings

        base = (settings.get("PLATFORM_URL") or "").strip()
        if not base:
            base = (settings.get("ADMIN_DOMAIN") or "").strip()
    except Exception:
        return ""
    if not base:
        return ""
    host = base.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0].lower()
    if host in ("localhost", "127.0.0.1", "::1"):
        return ""
    if "://" not in base:
        base = "https://" + base
    return base.rstrip("/")


def snippet(bot_id, origin=None):
    """The embed tag for one bot, or ``""`` when it cannot be built safely."""
    bot_id = str(bot_id or "").strip()
    origin = (origin if origin is not None else platform_origin()).rstrip("/")
    if not valid_bot_id(bot_id) or not origin:
        return ""
    return ('<script src="%s/widget.js?v=2" data-bot="%s"></script>'
            % (html.escape(origin, quote=True), html.escape(bot_id, quote=True)))


def inject(html_text, bot_id, origin=None):
    """Insert the widget into a page once, before ``</body>`` where possible."""
    text = html_text if isinstance(html_text, str) else str(html_text or "")
    tag = snippet(bot_id, origin=origin)
    if not tag or not text:
        return text
    bot_id = str(bot_id).strip()
    already = re.search(
        r"data-bot\s*=\s*(['\"])%s\1" % re.escape(bot_id), text, re.I)
    if already:
        return text
    if _WIDGET_SCRIPT.search(text):
        # The site was linked to a different bot; replace the old launcher rather
        # than stacking a second one.
        return _WIDGET_SCRIPT.sub(tag, text)
    match = _BODY_END.search(text)
    if match:
        return text[:match.start()] + tag + "\n" + text[match.start():]
    return text.rstrip() + "\n" + tag + "\n"


def strip(html_text):
    """Remove our embedded launcher (used when a site is unlinked)."""
    text = html_text if isinstance(html_text, str) else str(html_text or "")
    return _WIDGET_SCRIPT.sub("", text) if text else text


def bot_for_site(site):
    """The bot a site may embed, scoped to the site's tenant, or ``None``."""
    if not isinstance(site, dict):
        return None
    bot_id = str(site.get("linked_bot_id") or "").strip()
    org_id = site.get("org_id")
    if not bot_id or org_id is None or not valid_bot_id(bot_id):
        return None
    try:
        import bots

        return bots.get_bot(bot_id, org_id)
    except Exception:
        return None


def inject_for_site(html_text, site):
    """Inject a site's linked bot into page HTML when it belongs to that client."""
    bot = bot_for_site(site)
    if not bot:
        return strip(html_text)
    return inject(html_text, bot["id"])
