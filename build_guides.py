"""Plain-language build interviews: what to ask, what is required, what happens next.

The Manager should not improvise a website interview. Every guide here is deliberately
small and deterministic: the questions are the fields the builder actually uses, the
wording avoids web-builder jargon, and a required answer cannot be guessed into a build.

The module is pure. It has no database or HTTP imports, which keeps the validation the
same in the API, the Manager tool and the build task.
"""

import json
import re

KINDS = ("website", "blog", "form", "newsletter")

KIND_META = {
    "website": {
        "label": "Website",
        "blurb": "A simple one-page site with your services, contact details and optional forms.",
        "title": "a simple website",
    },
    "blog": {
        "label": "Blog post",
        "blurb": "An article written for your audience, saved as a draft or published on your site.",
        "title": "a blog post",
    },
    "form": {
        "label": "Sign-up form",
        "blurb": "A form that collects names and emails, and gives you a link to share.",
        "title": "a sign-up form",
    },
    "newsletter": {
        "label": "Newsletter",
        "blurb": "A newsletter email template, with an optional sign-up form and welcome email.",
        "title": "a newsletter",
    },
}

STEPS = {
    "website": ("The basics", "What you offer", "Contact and style", "Extras"),
    "blog": ("The idea", "The outline", "The finish"),
    "form": ("What it is for", "What to ask", "After they send", "Where it lives"),
    "newsletter": ("Who and why", "What to say", "Sign-up and follow-up"),
}

# Words a novice should not have to decode in the normal interview. The Advanced panel
# may use them; the question labels may not.
JARGON = ("cta", "seo", "slug", "html", "dns", "api", "smtp", "webhook", "css", "json")


def _q(qid, step, label, kind="text", **fields):
    question = {
        "id": qid,
        "step": step,
        "label": label,
        "type": kind,
        "example": "",
        "help": "",
        "required": False,
        "default": "",
        "options": [],
        "max_length": 2000 if kind != "long_text" else 8000,
    }
    question.update(fields)
    return question


def _options(*values):
    return [{"value": value, "label": value.replace("_", " ").capitalize()}
            for value in values]


QUESTIONS = {
    "website": [
        _q("business_name", 0, "What is your business called?",
           example="Acme Bakery", required=True, max_length=120,
           help="This is the name at the top of your site."),
        _q("what_you_do", 0, "In one sentence, what do you do?",
           kind="long_text", required=True,
           example="We bake fresh bread and custom cakes for local families.",
           help="Write it the way you would say it to a customer."),
        _q("audience", 0, "Who is your website for?", example="Local families and offices",
           help="A few words is enough. You can leave this blank."),

        _q("goal", 1, "What should a visitor do first?", kind="choice",
           options=_options("contact", "call", "book", "signup", "buy", "look", "other"),
           default="contact", required=True,
           help="Choose the one action that matters most."),
        _q("goal_other", 1, "What did you have in mind?", show_if={"id": "goal", "eq": "other"},
           example="Find our opening times"),
        _q("services", 1, "What do you offer?",
           kind="long_text", example="Fresh bread\nBirthday cakes\nOffice catering",
           help="Put one thing on each line. Two or three is plenty."),
        _q("different", 1, "What makes you different?",
           kind="long_text", example="Everything is baked the same morning.",
           help="Optional. One sentence is enough."),

        _q("email", 2, "What email should customers use?", kind="email",
           example="hello@acmebakery.com", help="We only use this on your own site."),
        _q("phone", 2, "What phone number should they call?", kind="phone",
           example="01234 567890"),
        _q("address", 2, "Where can people find you?", example="12 High Street, Bristol"),
        _q("hours", 2, "What are your opening hours?", example="Mon-Sat, 8am-5pm"),
        _q("style", 2, "Which look feels closest?", kind="choice",
           options=_options("clean", "warm", "bold", "minimal"), default="clean",
           required=True, help="You can change this later. We will keep it simple."),
        _q("brand_color", 2, "Pick a main colour", kind="color", default="#4f46e5",
           help="Used for buttons and small accents."),

        _q("site_size", 3, "How many pages do you need?", kind="choice",
           options=[{"value": "one_page", "label": "One simple page"},
                    {"value": "multi_page", "label": "A few pages (Home, About, Services, Contact)"}],
           default="one_page", required=True,
           help="Start with one page if you are not sure. You can add pages later."),
        _q("pages", 3, "Which pages should I make?", kind="multi",
           options=[{"value": "about", "label": "About"},
                    {"value": "services", "label": "Services or offers"},
                    {"value": "contact", "label": "Contact"},
                    {"value": "blog", "label": "Blog"}],
           default=["about", "services", "contact"],
           show_if={"id": "site_size", "eq": "multi_page"}, required=True,
           help="Home is always included."),
        _q("want_form", 3, "Should the site have a contact form?", kind="yes_no",
           default="yes", help="People can send you a message without opening their email."),
        _q("want_newsletter", 3, "Should the site collect newsletter sign-ups?", kind="yes_no",
           default="no", help="A small email sign-up form, with a welcome email."),
        _q("has_domain", 3, "Do you already have a web address?", kind="yes_no",
           default="no", help="Something like acmebakery.com. You can add one later."),
        _q("domain_name", 3, "What is the web address?", kind="domain",
           show_if={"id": "has_domain", "eq": "yes"}, example="acmebakery.com",
           help="Type it exactly, without https:// or www."),
    ],
    "blog": [
        _q("topic", 0, "What is the post about?", required=True,
           example="How to choose a birthday cake", max_length=200),
        _q("audience", 0, "Who is it for?", example="Parents planning a birthday",
           help="Optional, but it helps the writing."),
        _q("takeaway", 0, "What should they learn or do?",
           kind="long_text", required=True,
           example="How to pick a cake size and flavour without spending too much.",
           help="One or two sentences."),

        _q("points", 1, "What are the main points?", kind="long_text",
           example="Work out how many people you are feeding\nChoose a flavour everyone will like\nOrder a week ahead\nAsk about allergies",
           help="One point per line. Three to five is ideal."),
        _q("tone", 1, "What should it sound like?", kind="choice",
           options=_options("friendly", "professional", "bold"), default="friendly",
           required=True),
        _q("length", 1, "How long should it be?", kind="choice",
           options=_options("short", "standard", "detailed"), default="standard",
           required=True, help="Short is about 500 words; standard is about 800."),

        _q("cta", 2, "What should the reader do at the end?", kind="choice",
           options=_options("contact", "signup", "read", "none"), default="none",
           required=True),
        _q("author", 2, "Who should the post be signed by?", example="The Acme Bakery team",
           help="Optional. We will use your business name if you leave it blank."),
    ],
    "form": [
        _q("purpose", 0, "What is the form for?", kind="choice",
           options=_options("newsletter", "leads", "event", "quote", "survey", "other"),
           default="newsletter", required=True,
           help="Pick the closest one. It changes the questions we suggest."),
        _q("purpose_other", 0, "What is the form for?", show_if={"id": "purpose", "eq": "other"},
           example="Join our waiting list"),

        _q("fields", 1, "What should people fill in?", kind="multi",
           options=[{"value": "name", "label": "Their name"},
                    {"value": "email", "label": "Their email"},
                    {"value": "phone", "label": "Their phone"},
                    {"value": "message", "label": "A message"}],
           default=["name", "email"], required=True,
           help="Name and email are the usual minimum. Pick as many as you need."),
        _q("custom_question", 1, "Is there one more thing you want to ask?",
           example="Which service are you interested in?", help="Optional."),

        _q("success_message", 2, "What should they see after they send it?",
           kind="long_text", default="Thanks - we have your message and will be in touch.",
           example="Thanks! We will reply within one working day."),
        _q("button_label", 2, "What should the button say?", default="Send",
           example="Send my details"),
        _q("brand_color", 2, "Pick a main colour", kind="color", default="#4f46e5"),
        _q("welcome_email", 2, "Should they get a welcome email?", kind="yes_no",
           default="yes", help="We write it now; you review it before anything is sent."),
        _q("welcome_subject", 2, "What should the welcome email subject be?",
           show_if={"id": "welcome_email", "eq": "yes"},
           default="Thanks for getting in touch"),
        _q("welcome_message", 2, "What should the welcome email say?",
           kind="long_text", show_if={"id": "welcome_email", "eq": "yes"},
           default="Thanks for getting in touch. We will reply as soon as we can."),

        _q("place", 3, "Where will people find it?", kind="choice",
           options=[{"value": "site", "label": "On my website"},
                    {"value": "link", "label": "A link I can share"},
                    {"value": "embed", "label": "Embedded on another page"}],
           default="link", required=True),
    ],
    "newsletter": [
        _q("audience", 0, "Who is this newsletter for?", example="Customers who like our bread",
           help="Optional, but it helps the writing."),
        _q("goal", 0, "What should the newsletter do?",
           example="Keep people coming back to the bakery",
           required=True, help="For example: welcome people, share an offer, or teach something."),

        _q("subject", 1, "What should the subject line say?", required=True,
           example="A small treat for the weekend", max_length=200),
        _q("story", 1, "What is the main thing you want to share?",
           kind="long_text", required=True,
           example="Our new sourdough is ready, and we are open late on Fridays."),
        _q("offer", 1, "Is there an offer or next step?", example="10% off your first loaf",
           help="Optional."),
        _q("links", 1, "Any links to include?", kind="long_text",
           example="https://acmebakery.com/menu", help="One per line. Optional."),
        _q("tone", 1, "What should it sound like?", kind="choice",
           options=_options("friendly", "professional", "bold"), default="friendly",
           required=True),
        _q("length", 1, "How long should it be?", kind="choice",
           options=_options("short", "standard", "detailed"), default="standard",
           required=True),

        _q("need_form", 2, "Do you need a sign-up form too?", kind="yes_no",
           default="yes", help="A small form that adds people to your contacts."),
        _q("form_fields", 2, "What should the sign-up ask for?", kind="multi",
           options=[{"value": "name", "label": "Their name"},
                    {"value": "email", "label": "Their email"},
                    {"value": "phone", "label": "Their phone"}],
           default=["name", "email"], show_if={"id": "need_form", "eq": "yes"},
           required=True),
        _q("welcome", 2, "Should I draft a welcome email and a follow-up automation?",
           kind="yes_no", default="yes",
           help="The automation is saved switched off. You review it before enabling."),
        _q("sequence", 2, "How much follow-up do you want?", kind="choice",
           options=[{"value": "welcome", "label": "Just a welcome email"},
                    {"value": "series", "label": "A short welcome series (3 emails)"}],
           default="welcome", show_if={"id": "welcome", "eq": "yes"}, required=True,
           help="A series is three short emails spaced a few days apart."),
        _q("followup", 2, "What should the follow-up emails talk about?",
           kind="long_text", show_if={"id": "sequence", "eq": "series"},
           default="Share one useful tip and invite them back.",
           help="We will turn this into two short follow-ups."),
        _q("cadence", 2, "How often should the series send?", kind="choice",
           options=[{"value": "3_days", "label": "Every 3 days"},
                    {"value": "7_days", "label": "Every week"}],
           default="3_days", show_if={"id": "sequence", "eq": "series"},
           required=True),
        _q("welcome_message", 2, "What should the welcome email say?",
           kind="long_text", show_if={"id": "welcome", "eq": "yes"},
           default="Thanks for joining us. Here is what to expect from our newsletter.",
           help="Optional - we can use a short default."),
        _q("brand_color", 2, "Pick a main colour", kind="color", default="#4f46e5"),
    ],
}


def normalise_kind(value):
    kind = str(value or "").strip().lower()
    return kind if kind in KINDS else ""


def meta(kind):
    return KIND_META.get(normalise_kind(kind)) or {}


def _shown(question, answers):
    condition = question.get("show_if")
    if not condition:
        return True
    qid = str(condition.get("id") or "")
    got = (answers or {}).get(qid)
    if isinstance(got, list):
        got = ",".join(str(item) for item in got)
    got = str(got if got is not None else "").strip().lower()
    if "in" in condition:
        return got in {str(item).strip().lower() for item in (condition.get("in") or [])}
    return got == str(condition.get("eq", "") or "").strip().lower()


def _empty(value):
    if isinstance(value, list):
        return not [item for item in value if str(item).strip()]
    return not str(value if value is not None else "").strip()


def questions_for(kind, step, answers=None):
    """The questions on one step that are visible for these answers."""
    kind = normalise_kind(kind)
    if not kind:
        return []
    answers = answers or {}
    try:
        wanted = int(step)
    except (TypeError, ValueError):
        wanted = 0
    return [q for q in QUESTIONS[kind]
            if int(q.get("step") or 0) == wanted and _shown(q, answers)]


def effective_answers(kind, answers):
    """Only answers that are still visible, with defaults filled in."""
    kind = normalise_kind(kind)
    answers = dict(answers or {})
    out = {}
    for question in QUESTIONS.get(kind, []):
        if not _shown(question, answers):
            continue
        value = answers.get(question["id"], "")
        if _empty(value) and question.get("default") not in (None, ""):
            value = question.get("default")
        if not _empty(value):
            out[question["id"]] = value
    return out


def _option_values(question):
    return {str(option.get("value") if isinstance(option, dict) else option)
            for option in (question.get("options") or [])}


def _clean_value(question, value):
    """Normalise one answer, or raise ValueError with a sentence for the user."""
    kind = question.get("type") or "text"
    if kind == "multi":
        if isinstance(value, str):
            value = [part.strip() for part in value.split(",") if part.strip()]
        if value in (None, ""):
            value = []
        if not isinstance(value, list):
            raise ValueError("Choose from the list.")
        allowed = _option_values(question)
        value = [str(item).strip() for item in value if str(item).strip()]
        if [item for item in value if item not in allowed]:
            raise ValueError("Choose one of the listed options.")
        return value

    text = str(value if value is not None else "").strip()
    if not text:
        return ""
    if kind in ("choice", "yes_no"):
        text = text.lower()
        allowed = {"yes", "no"} if kind == "yes_no" else _option_values(question)
        if text not in allowed:
            raise ValueError("Choose one of the listed options.")
    if kind in ("text", "long_text"):
        limit = int(question.get("max_length") or 2000)
        if len(text) > limit:
            raise ValueError("That answer is too long - keep it under %d characters." % limit)
    if kind == "email" and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", text):
        raise ValueError("That email does not look right.")
    if kind == "domain":
        text = text.lower().replace("https://", "").replace("http://", "")
        text = text.split("/", 1)[0].strip().strip(".")
        if not re.match(r"^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}$", text):
            raise ValueError("That web address does not look right - try example.com.")
    if kind == "phone":
        if len(re.sub(r"\D", "", text)) < 6 or not re.match(r"^[0-9+() .-]+$", text):
            raise ValueError("That phone number does not look right.")
    if kind == "color":
        if not re.match(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$", text):
            raise ValueError("Pick a colour like #4f46e5.")
        text = text.lower()
    if kind == "number":
        try:
            int(text)
        except ValueError:
            raise ValueError("That should be a number.")
    return text


def validate_step(kind, step, answers):
    """Validate and normalise the answers submitted for one step.

    Returns ``(clean, errors)``. The clean values are what the next question's
    ``show_if`` sees, so a domain cannot be required when "no domain" was chosen.
    """
    kind = normalise_kind(kind)
    if not kind:
        return {}, ["Choose what you want to build."]
    merged = dict(answers or {})
    clean = {}
    errors = []
    for question in questions_for(kind, step, merged):
        try:
            value = _clean_value(question, merged.get(question["id"]))
        except ValueError as exc:
            errors.append("%s %s" % (question["label"], exc))
            continue
        if not value and question.get("default") not in (None, ""):
            value = question.get("default")
        if not value and question.get("required"):
            errors.append("%s is needed." % question["label"])
            continue
        clean[question["id"]] = value
    if errors:
        return {}, errors
    merged.update(clean)
    return merged, []


def first_missing_step(kind, answers):
    """The first step with a visible required question that has no answer, or None."""
    for index in range(len(STEPS.get(normalise_kind(kind), ()))):
        for question in questions_for(kind, index, answers):
            if not question.get("required"):
                continue
            if _empty((answers or {}).get(question["id"])) and \
                    question.get("default") in (None, ""):
                return index
    return None


def advance(kind, answers, from_step):
    """The next step to show after a successful submit."""
    total = len(STEPS.get(normalise_kind(kind), ()))
    try:
        step = int(from_step) + 1
    except (TypeError, ValueError):
        step = 0
    while step < total and not questions_for(kind, step, answers):
        step += 1
    missing = first_missing_step(kind, answers)
    if step >= total and missing is not None:
        return missing
    return min(step, total)


def ready(kind, answers):
    return first_missing_step(kind, answers) is None


def title_for(kind, answers):
    kind = normalise_kind(kind)
    answers = effective_answers(kind, answers)
    if kind == "website":
        return "Website for %s" % (answers.get("business_name") or "your business")
    if kind == "blog":
        return answers.get("topic") or "Blog post"
    if kind == "form":
        purpose = answers.get("purpose_other") or str(answers.get("purpose") or "sign-up")
        return "%s form" % purpose.replace("_", " ").title()
    if kind == "newsletter":
        return "Newsletter: %s" % (answers.get("subject") or "your first issue")
    return "New build"


def summary(kind, answers):
    """What the review screen says, in the same plain language as the questions."""
    kind = normalise_kind(kind)
    answers = effective_answers(kind, answers)
    will = []
    assumed = []
    missing = []
    if kind == "website":
        if answers.get("site_size") == "multi_page":
            pages = answers.get("pages") or ["about", "services", "contact"]
            will.append("A website for %s with Home, %s" %
                        (answers.get("business_name") or "your business",
                         ", ".join(page.title() for page in pages)))
        else:
            will.append("A one-page website for %s" %
                        (answers.get("business_name") or "your business"))
        if answers.get("services"):
            will.append("A section for what you offer")
        if answers.get("email") or answers.get("phone"):
            will.append("Your contact details")
        if answers.get("want_form") == "yes":
            will.append("A contact form with its own link")
        if answers.get("want_newsletter") == "yes":
            will.append("A newsletter sign-up form and a welcome email")
        if answers.get("has_domain") == "yes":
            will.append("A place for %s" % answers.get("domain_name"))
        else:
            missing.append("A web address - you can add one after seeing the preview.")
        assumed.append("A clean, responsive layout in %s" % (answers.get("style") or "clean"))
        assumed.append("Your main colour: %s" % (answers.get("brand_color") or "#4f46e5"))
    elif kind == "blog":
        will.append("A blog post called %r" % (answers.get("topic") or "your article"))
        will.append("A clean article page you can review")
        if answers.get("points"):
            will.append("The main points you listed")
        if answers.get("cta") and answers.get("cta") != "none":
            will.append("A closing call to action")
        assumed.append("A %s article in a %s voice" %
                       (answers.get("length") or "standard",
                        answers.get("tone") or "friendly"))
        missing.append("A site to publish on - the post will be saved as a draft first.")
    elif kind == "form":
        fields = answers.get("fields") or ["name", "email"]
        will.append("A %s form asking for %s" %
                    (str(answers.get("purpose") or "sign-up").replace("_", " "),
                     ", ".join(fields)))
        will.append("A shareable link")
        if answers.get("place") == "site":
            will.append("A note explaining how to put it on your site")
        if answers.get("welcome_email") == "yes":
            will.append("A welcome email for you to review")
        assumed.append("The message: %s" %
                       (answers.get("success_message") or "Thanks - we have your message."))
        if answers.get("welcome_email") == "yes":
            missing.append("A sender address before the welcome email can be sent.")
    elif kind == "newsletter":
        will.append("A newsletter template called %r" %
                    (answers.get("subject") or "your first issue"))
        if answers.get("need_form") == "yes":
            will.append("A sign-up form that adds people to your contacts")
        if answers.get("welcome") == "yes":
            will.append("A welcome email and an automation saved switched off")
            if answers.get("sequence") == "series":
                will.append("Two follow-up emails in the same switched-off series")
        assumed.append("A %s email in a %s voice" %
                       (answers.get("length") or "standard",
                        answers.get("tone") or "friendly"))
        missing.append("A sender address and a review before anything is sent.")
    return {
        "title": title_for(kind, answers),
        "will_build": will,
        "assumptions": assumed,
        "missing": missing,
        "publish_note": ("Saving creates a draft. Nothing goes public until you press "
                         "Publish." if kind in ("website", "blog") else
                         "We save the pieces first. Sending still needs your approval."),
    }


def payload(kind, answers, step=0, status="asking", result=None, build_id=0):
    """The JSON the console needs to render one step, or the review."""
    kind = normalise_kind(kind)
    labels = list(STEPS.get(kind, ()))
    answers = dict(answers or {})
    effective = effective_answers(kind, answers)
    total = len(labels)
    try:
        current = max(0, min(int(step), total))
    except (TypeError, ValueError):
        current = 0
    show_review = status in ("ready", "building", "done", "error") or current >= total
    questions = [] if show_review else questions_for(kind, current, effective)
    rendered = []
    for question in questions:
        value = effective.get(question["id"], question.get("default") or "")
        if isinstance(value, list):
            value = list(value)
        rendered.append({
            "id": question["id"],
            "label": question["label"],
            "type": question["type"],
            "options": list(question.get("options") or []),
            "required": bool(question.get("required")),
            "example": question.get("example") or "",
            "help": question.get("help") or "",
            "default": question.get("default") or "",
            "value": value,
        })
    return {
        "id": int(build_id or 0),
        "kind": kind,
        "label": meta(kind).get("label") or kind,
        "title": title_for(kind, effective),
        "status": status,
        "step": current,
        "total_steps": total,
        "step_label": labels[current] if current < total else "Review",
        "steps": [{"index": index, "label": label} for index, label in enumerate(labels)],
        "progress": {"step": min(current + 1, total) if total else 0, "total": total},
        "questions": rendered,
        "answers": effective,
        "summary": summary(kind, effective) if show_review else None,
        "result": result or {},
        "ready": ready(kind, effective),
    }


def seed_answers(kind, profile=None, goal=""):
    """Prefill what the Business Profile already knows, without inventing facts."""
    kind = normalise_kind(kind)
    profile = profile or {}
    answers = {}
    if profile.get("name"):
        answers["business_name"] = profile.get("name")
    if profile.get("description"):
        answers["what_you_do"] = profile.get("description")
    if profile.get("target_audience"):
        answers["audience"] = profile.get("target_audience")
    if profile.get("support_email"):
        answers["email"] = profile.get("support_email")
    if profile.get("support_phone"):
        answers["phone"] = profile.get("support_phone")
    if profile.get("address"):
        answers["address"] = profile.get("address")
    if profile.get("business_hours"):
        answers["hours"] = profile.get("business_hours")
    if profile.get("brand_color"):
        answers["brand_color"] = profile.get("brand_color")
    if kind == "blog" and profile.get("name"):
        answers["author"] = profile.get("name")
    services = profile.get("services") or []
    if isinstance(services, str):
        try:
            services = json.loads(services or "[]")
        except ValueError:
            services = []
    if kind == "website" and isinstance(services, list) and services:
        lines = []
        for item in services:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                desc = str(item.get("description") or "").strip()
                if name:
                    lines.append("%s%s" % (name, (" - " + desc) if desc else ""))
            elif str(item).strip():
                lines.append(str(item).strip())
        if lines:
            answers["services"] = "\n".join(lines[:6])
    voice = str(profile.get("tone_of_voice") or "").lower()
    for style in ("warm", "bold", "minimal", "clean"):
        if style in voice:
            answers["style"] = style
            break
    low = str(goal or "").lower()
    if kind == "website":
        for needle, value in (("book", "book"), ("call", "call"), ("buy", "buy"),
                              ("shop", "buy"), ("sign up", "signup"), ("signup", "signup"),
                              ("subscribe", "signup"), ("look", "look")):
            if needle in low:
                answers["goal"] = value
                break
    if kind == "form":
        for needle, value in (("event", "event"), ("survey", "survey"),
                              ("quote", "quote"), ("lead", "leads"),
                              ("newsletter", "newsletter"), ("sign up", "newsletter")):
            if needle in low:
                answers["purpose"] = value
                break
    return answers


def check_language():
    """Question labels that slipped into jargon. Used by the test, not at runtime."""
    bad = []
    for kind, questions in QUESTIONS.items():
        for question in questions:
            label = str(question.get("label") or "").lower()
            for word in JARGON:
                if re.search(r"\b%s\b" % re.escape(word), label):
                    bad.append("%s/%s: %s" % (kind, question.get("id"), word))
    return bad

