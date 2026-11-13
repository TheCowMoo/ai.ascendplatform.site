"""Deterministic page and email templates for the guided builder.

The user answers plain-language questions; this module turns those answers into safe,
responsive HTML. Keeping the layout here rather than in a model prompt is what makes a
novice's first site predictable: there are no placeholders to fill in, no CSS to learn,
and no way for an answer to become markup.
"""

import datetime
import html
import re

import provisioner

STYLE_FONTS = {
    "clean": "system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
    "warm": "Georgia, 'Times New Roman', serif",
    "bold": "system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
    "minimal": "system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
}


def _text(value):
    return "\n".join(line.strip() for line in str(value or "").splitlines() if line.strip())


def _inline(value):
    return html.escape(_text(value), quote=True)


def _lines(value):
    return [line.strip() for line in str(value or "").splitlines() if line.strip()]


def _paragraphs(value):
    return "".join("<p>%s</p>" % html.escape(line, quote=True) for line in _lines(value))


def _href(value):
    return html.escape(str(value or ""), quote=True)


def _button(url, label, color="#4f46e5"):
    return ('<p style="text-align:center;margin:32px 0">'
            '<a href="%s" style="display:inline-block;padding:14px 24px;border-radius:999px;'
            'background:%s;color:#ffffff;text-decoration:none;font-weight:650">%s</a></p>'
            % (_href(url), _href(color), html.escape(label)))


def _style_font(style):
    return STYLE_FONTS.get(str(style or "clean").lower(), STYLE_FONTS["clean"])


def render_website(answers, profile=None, form_links=None):
    """A one-page site from the guide answers.

    ``form_links`` is a list of ``{"label", "url"}`` dictionaries. Every value from the
    user is escaped before it reaches the template; the only markup is this module's.
    """
    answers = answers or {}
    profile = profile or {}
    name = answers.get("business_name") or profile.get("name") or "Your business"
    intro = answers.get("what_you_do") or profile.get("description") or ""
    audience = answers.get("audience") or profile.get("target_audience") or ""
    services = _lines(answers.get("services"))
    different = _text(answers.get("different"))
    email = answers.get("email") or profile.get("support_email") or ""
    phone = answers.get("phone") or profile.get("support_phone") or ""
    address = answers.get("address") or profile.get("address") or ""
    hours = answers.get("hours") or profile.get("business_hours") or ""
    color = answers.get("brand_color") or profile.get("brand_color") or "#4f46e5"

    parts = []
    if intro:
        parts.append("<p>%s</p>" % html.escape(intro))
    if audience:
        parts.append("<h2>Who we help</h2><p>%s</p>" % html.escape(audience))
    if services:
        parts.append("<h2>What we do</h2><ul>%s</ul>" %
                     "".join("<li>%s</li>" % html.escape(item) for item in services))
    if different:
        parts.append("<h2>Why choose us</h2>%s" % _paragraphs(different))

    contact = []
    if email:
        contact.append('<p>Email: <a href="mailto:%s">%s</a></p>' %
                       (_href(email), html.escape(email)))
    if phone:
        digits = re.sub(r"[^0-9+]", "", phone)
        contact.append('<p>Phone: <a href="tel:%s">%s</a></p>' %
                       (_href(digits), html.escape(phone)))
    if address:
        contact.append("<p>%s</p>" % html.escape(address))
    if hours:
        contact.append("<p>%s</p>" % html.escape(hours))
    if contact:
        parts.append("<h2>Contact</h2>%s" % "".join(contact))

    links = list(form_links or [])
    if not links and email:
        links.append({"label": "Get in touch", "url": "mailto:%s" % email})
    elif not links and phone:
        links.append({"label": "Call us", "url": "tel:%s" % re.sub(r"[^0-9+]", "", phone)})
    for link in links:
        if link.get("url"):
            parts.append(_button(link["url"], link.get("label") or "Get in touch", color))

    return provisioner.render_index(
        title=name,
        tagline=intro or "Welcome",
        body_html="\n".join(parts) or "<p>We are getting things ready.</p>",
        brand_color=color,
        font_family=_style_font(answers.get("style")),
    )


def render_blog(answers, profile=None, cta_links=None):
    """A clean article page, with a narrower reading column than the site template."""
    answers = answers or {}
    profile = profile or {}
    title = answers.get("topic") or "A new post"
    audience = answers.get("audience") or profile.get("target_audience") or ""
    takeaway = _text(answers.get("takeaway"))
    points = _lines(answers.get("points"))
    author = answers.get("author") or profile.get("name") or ""
    color = answers.get("brand_color") or profile.get("brand_color") or "#4f46e5"
    ink = provisioner.readable_ink(color)
    soft = provisioner.soft_tint(color, 0.14)
    font = _style_font("warm" if answers.get("tone") == "professional" else "clean")
    today = datetime.date.today().strftime("%B %d, %Y")
    meta = [html.escape(author)] if author else []
    meta += [today, "A %s article" % html.escape(answers.get("length") or "standard")]

    body = []
    if takeaway:
        body.append('<p class="lede">%s</p>' % html.escape(takeaway))
    if points:
        body.append("<h2>In short</h2><ul>%s</ul>" %
                    "".join("<li>%s</li>" % html.escape(item) for item in points))
    if audience:
        body.append("<h2>Who this is for</h2><p>%s</p>" % html.escape(audience))
    for link in cta_links or []:
        if link.get("url"):
            body.append(_button(link["url"], link.get("label") or "Get in touch", color))

    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%s</title>
<meta name="description" content="%s">
<style>
  :root { --brand: %s; --ink: %s; --soft: %s; --text: #1f2937; --muted: #5b6472; --line: #e6e8ec; }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: %s; color: var(--text); background: #fff; line-height: 1.7; }
  .wrap { max-width: 720px; margin: 0 auto; padding: 0 24px; }
  header { padding: 72px 0 48px; border-bottom: 1px solid var(--line); background: linear-gradient(180deg, var(--soft), transparent); }
  .eyebrow { color: var(--muted); font-size: 12px; letter-spacing: .08em; text-transform: uppercase; font-weight: 700; }
  h1 { margin: 10px 0 14px; font-size: 42px; line-height: 1.12; letter-spacing: -0.03em; color: #0b1120; }
  .meta { color: var(--muted); font-size: 14px; }
  main { padding: 40px 0 24px; }
  .lede { font-size: 20px; color: #374151; }
  h2 { margin: 36px 0 10px; font-size: 22px; color: #0b1120; }
  li { margin-bottom: 8px; }
  a { color: var(--brand); }
  footer { border-top: 1px solid var(--line); margin-top: 48px; padding: 24px 0 48px; color: var(--muted); font-size: 13px; }
</style>
</head>
<body>
<header><div class="wrap"><div class="eyebrow">%s</div><h1>%s</h1><div class="meta">%s</div></div></header>
<main><div class="wrap">%s</div></main>
<footer><div class="wrap">Written for %s.</div></footer>
</body>
</html>
""" % (html.escape(title), _href(takeaway), html.escape(color), html.escape(ink),
       html.escape(soft), font, html.escape(author or "A helpful read"),
       html.escape(title), " · ".join(meta),
       "\n".join(body) or "<p>This article is ready for its first draft.</p>",
       html.escape(audience or "your readers"))


def render_newsletter(answers, profile=None):
    """Subject and plain-text body for the newsletter template.

    Plain text is deliberate for the first release: the send path is text today, and a
    draft that looks designed but cannot be sent as designed is worse than an honest
    text draft. A later release can add the HTML wrapper to this same content.
    """
    answers = answers or {}
    profile = profile or {}
    name = profile.get("name") or ""
    subject = answers.get("subject") or "A note from %s" % (name or "us")
    story = _text(answers.get("story"))
    offer = _text(answers.get("offer"))
    links = _lines(answers.get("links"))
    greeting = "Hello,"
    parts = [greeting, "", story]
    if offer:
        parts += ["", offer]
    if links:
        parts += ["", "Links:"] + links
    parts += ["", "Speak soon,", name or "The team"]
    return {"subject": subject, "body": "\n".join(parts).strip()}


def render_welcome(answers, profile=None):
    """Subject and body for the welcome email a form or newsletter offers."""
    answers = answers or {}
    profile = profile or {}
    name = profile.get("name") or ""
    subject = answers.get("welcome_subject") or "Thanks for getting in touch"
    body = _text(answers.get("welcome_message")) or \
        "Thanks for getting in touch. We will reply as soon as we can."
    return {
        "subject": subject,
        "body": "%s\n\n%s" % (body, name or "The team"),
    }
