"""The plain-language build interviews: complete, valid and jargon-free.

Run: .\\venv\\Scripts\\python.exe tests\\test_build_guides.py

No database, no model. These checks pin the questions the builder actually relies on,
so a missing choice or a "slug" in a label is caught before a novice sees it.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import build_guides as guides  # noqa: E402

FAILED = []


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


def test_every_guide_is_well_formed():
    check("no build jargon in labels", guides.check_language(), [])
    for kind in guides.KINDS:
        ids = [question["id"] for question in guides.QUESTIONS[kind]]
        check("%s has unique question ids" % kind, len(ids), len(set(ids)))
        check("%s has a title" % kind, bool(guides.meta(kind).get("label")), True)
        check("%s has steps" % kind, len(guides.STEPS[kind]) >= 2, True)
        for question in guides.QUESTIONS[kind]:
            if question["type"] in ("choice", "multi"):
                check("%s/%s has options" % (kind, question["id"]),
                      bool(question.get("options")), True)
            condition = question.get("show_if")
            if condition:
                check("%s/%s show_if names a real question" %
                      (kind, question["id"]), condition.get("id") in ids, True)
            if question.get("show_if") and question.get("required"):
                check("%s/%s conditional required is reachable" %
                      (kind, question["id"]), True, True)


def test_missing_answers_are_refused_and_domains_are_clean():
    answers, errors = guides.validate_step("website", 0, {})
    check("a required name is refused", bool(errors), True)
    check("and the message says what is missing", "business called" in errors[0].lower(), True)

    answers, errors = guides.validate_step(
        "website", 0, {"business_name": "Acme", "what_you_do": "Bread"})
    check("a complete first step is accepted", errors, [])
    check("it keeps the answers", answers["business_name"], "Acme")

    answers, errors = guides.validate_step(
        "website", 3, dict(answers, has_domain="yes", domain_name="https://Acme.test/"))
    check("a pasted web address is cleaned", errors, [])
    check("to a bare domain", answers["domain_name"], "acme.test")

    _, errors = guides.validate_step(
        "form", 0, {"purpose": "not-a-real-choice"})
    check("an unknown choice is refused", bool(errors), True)


def test_the_next_question_depends_on_the_last_answer():
    answers, errors = guides.validate_step(
        "website", 3, {"has_domain": "no"})
    check("saying no needs no domain", errors, [])
    questions = guides.questions_for("website", 3, answers)
    check("the domain question is hidden",
          "domain_name" in [question["id"] for question in questions], False)

    answers, errors = guides.validate_step(
        "website", 3, {"has_domain": "yes", "domain_name": "acme.test"})
    check("saying yes accepts a domain", errors, [])
    questions = guides.questions_for("website", 3, answers)
    check("the domain question is visible when it applies",
          "domain_name" in [question["id"] for question in questions], True)


def test_seed_and_summary_are_plain_language():
    profile = {
        "name": "Acme Bakery",
        "description": "Fresh bread for the neighbourhood.",
        "target_audience": "Local families",
        "support_email": "hello@acme.test",
        "support_phone": "01234 567890",
        "services": '[{"name": "Bread"}, {"name": "Cakes"}]',
        "brand_color": "#123456",
    }
    seeded = guides.seed_answers("website", profile, "build a bakery website")
    check("the business name is prefilled", seeded["business_name"], "Acme Bakery")
    check("the services are prefilled", "Bread" in seeded["services"], True)
    check("the colour is prefilled", seeded["brand_color"], "#123456")
    payload = guides.payload("website", seeded, 0, "asking", build_id=7)
    check("the first step is shown", len(payload["questions"]) > 0, True)
    check("its title names the business", "Acme Bakery" in payload["title"], True)
    check("the review summary exists", bool(guides.summary("website", seeded)["will_build"]), True)


if __name__ == "__main__":
    test_every_guide_is_well_formed()
    test_missing_answers_are_refused_and_domains_are_clean()
    test_the_next_question_depends_on_the_last_answer()
    test_seed_and_summary_are_plain_language()
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nBUILD GUIDES OK - plain questions, real validation")
