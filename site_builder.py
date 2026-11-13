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


def _nav(answers, current):
    labels = {"home": "Home", "about": "About", "services": "Services",
              "contact": "Contact", "blog": "Blog"}
    pages = ["home"]
    for page in answers.get("pages") or []:
        if page in labels and page != "home" and page not in pages:
            pages.append(page)
    links = []
    for page in pages:
        href = "/" if page == "home" else "/%s.html" % page
        if page == current:
            links.append('<span class="site-nav-current">%s</span>' % labels[page])
        else:
            links.append('<a href="%s">%s</a>' % (href, labels[page]))
    return ('<style>.site-nav{display:flex;gap:18px;flex-wrap:wrap;margin:0 0 28px;'
            'padding:0 0 14px;border-bottom:1px solid #e6e8ec;font-size:14px;'
            'font-weight:650}.site-nav a{text-decoration:none}.site-nav-current{'
            'opacity:.6}.blog-list{display:grid;gap:16px}.blog-list article{'
            'padding:16px;border:1px solid #e6e8ec;border-radius:12px}'
            '.blog-list h2{margin:0 0 6px;font-size:20px}</style>'
            '<nav class="site-nav" aria-label="Site">%s</nav>' % "".join(links))


def _contact_html(answers, profile):
    email = answers.get("email") or profile.get("support_email") or ""
    phone = answers.get("phone") or profile.get("support_phone") or ""
    address = answers.get("address") or profile.get("address") or ""
    hours = answers.get("hours") or profile.get("business_hours") or ""
    parts = []
    if email:
        parts.append('<p>Email: <a href="mailto:%s">%s</a></p>' %
                     (_href(email), html.escape(email)))
    if phone:
        parts.append('<p>Phone: <a href="tel:%s">%s</a></p>' %
                     (_href(re.sub(r"[^0-9+]", "", phone)), html.escape(phone)))
    if address:
        parts.append("<p>%s</p>" % html.escape(address))
    if hours:
        parts.append("<p>%s</p>" % html.escape(hours))
    return "".join(parts)


def _services_html(answers):
    services = _lines(answers.get("services"))
    return ("<ul>%s</ul>" %
            "".join("<li>%s</li>" % html.escape(item) for item in services)
            if services else "")


def _cta_html(form_links, color):
    parts = []
    for link in form_links or []:
        if link.get("url"):
            parts.append(_button(link["url"], link.get("label") or "Get in touch", color))
    return "".join(parts)


def render_site_page(answers, page="home", profile=None, form_links=None, blog_posts=None):
    """One page of a multi-page starter site, with a consistent navigation bar."""
    answers = answers or {}
    profile = profile or {}
    name = answers.get("business_name") or profile.get("name") or "Your business"
    intro = answers.get("what_you_do") or profile.get("description") or ""
    audience = answers.get("audience") or profile.get("target_audience") or ""
    different = _text(answers.get("different"))
    color = answers.get("brand_color") or profile.get("brand_color") or "#4f46e5"
    nav = _nav(answers, page)
    contact = _contact_html(answers, profile)
    services = _services_html(answers)
    cta = _cta_html(form_links, color)
    titles = {
        "home": name,
        "about": "About %s" % name,
        "services": "What we do",
        "contact": "Contact",
        "blog": "Blog",
    }
    taglines = {
        "home": intro or "Welcome",
        "about": audience or "A little about us",
        "services": "A few ways we can help.",
        "contact": "We would love to hear from you.",
        "blog": "News and useful reads.",
    }
    body = [nav]
    if page == "home":
        if services:
            body.append("<h2>What we do</h2>%s" % services)
        if different:
            body.append("<h2>Why choose us</h2>%s" % _paragraphs(different))
        if contact:
            body.append("<h2>Contact</h2>%s" % contact)
        body.append(cta)
    elif page == "about":
        if intro:
            body.append("<p>%s</p>" % html.escape(intro))
        if audience:
            body.append("<h2>Who we help</h2><p>%s</p>" % html.escape(audience))
        if different:
            body.append("<h2>What makes us different</h2>%s" % _paragraphs(different))
        body.append(cta)
    elif page == "services":
        body.append("<h2>What we offer</h2>%s" % (services or "<p>Details coming soon.</p>"))
        if different:
            body.append("<h2>Why us</h2>%s" % _paragraphs(different))
        body.append(cta)
    elif page == "contact":
        body.append("<h2>Get in touch</h2>%s" % (contact or
                    "<p>Add your contact details in the Business Profile.</p>"))
        body.append(cta)
    elif page == "blog":
        posts = list(blog_posts or [])
        if not posts:
            body.append("<p>Our first post is on its way.</p>")
        else:
            cards = []
            for post in posts:
                slug = post.get("slug") or ""
                href = "/%s.html" % slug
                cards.append('<article><h2><a href="%s">%s</a></h2><p>%s</p></article>'
                             % (_href(href), html.escape(post.get("title") or "Post"),
                                html.escape(post.get("summary") or "")))
            body.append('<div class="blog-list">%s</div>' % "".join(cards))
    return provisioner.render_index(
        title=titles.get(page, name),
        tagline=taglines.get(page, ""),
        body_html="".join(body),
        brand_color=color,
        font_family=_style_font(answers.get("style")),
    )


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


def _safe_url(value):
    text = str(value or "").strip()
    if re.match(r"^(https?://|mailto:|tel:|/)", text, re.I):
        return _href(text)
    return ""


def render_newsletter_html(answers, profile=None):
    """A brand-wrapped HTML email for the newsletter template.

    Inline styles only: mail clients strip stylesheets. Every answer is escaped, and a
    link is only emitted when it is http(s), mailto, tel or relative.
    """
    answers = answers or {}
    profile = profile or {}
    name = profile.get("name") or answers.get("business_name") or "Our newsletter"
    color = answers.get("brand_color") or profile.get("brand_color") or "#4f46e5"
    intro = _text(answers.get("story"))
    offer = _text(answers.get("offer"))
    links = [link for link in _lines(answers.get("links")) if _safe_url(link)]
    parts = [
        '<div style="max-width:640px;margin:0 auto;background:#ffffff;'
        'border:1px solid #e6e8ec;border-radius:14px;overflow:hidden;'
        'font-family:system-ui,-apple-system,\'Segoe UI\',Roboto,Arial,sans-serif;'
        'color:#1f2937;line-height:1.65">',
        '<div style="background:%s;color:#ffffff;padding:28px 30px">'
        '<div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;'
        'opacity:.85">%s</div>'
        '<h1 style="margin:6px 0 0;font-size:26px;line-height:1.2">%s</h1></div>'
        % (html.escape(color), html.escape(name), html.escape(answers.get("subject") or name)),
        '<div style="padding:28px 30px">',
    ]
    if intro:
        parts.append("<p style='margin:0 0 16px'>%s</p>" % html.escape(intro).replace("\n", "<br>"))
    if offer:
        parts.append('<p style="margin:0 0 16px;padding:12px 14px;background:#f7f7fb;'
                     'border-radius:10px">%s</p>' % html.escape(offer).replace("\n", "<br>"))
    if links:
        parts.append("<p style='margin:0 0 8px;font-weight:650'>Links</p><ul style='margin:0;"
                     "padding-left:20px'>")
        for link in links:
            parts.append('<li><a href="%s" style="color:%s">%s</a></li>'
                         % (_safe_url(link), html.escape(color), html.escape(link)))
        parts.append("</ul>")
    parts.append("</div>")
    parts.append('<div style="padding:18px 30px;background:#fbfbfd;color:#6b7280;'
                 'font-size:12px">Sent by %s.</div>' % html.escape(name))
    parts.append("</div>")
    return ('<!DOCTYPE html><html><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>%s</title></head><body style="margin:0;padding:24px;'
            'background:#f3f4f6">%s</body></html>'
            % (html.escape(answers.get("subject") or name), "".join(parts)))


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
