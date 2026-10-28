"""The form engine, and the service that stores what it decides.

Branching is the part that cannot be eyeballed: a rule that points backwards, a page jump, a
score that only counts the questions a visitor was actually asked. These tests fix the rules
in one place so the hosted page, the submit endpoint and the editor cannot drift apart.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("ALLOW_LIVE_ACTIONS", None)

import forms_engine
from forms_engine import FormError

FAILED = []


def check(name, got, want):
    if got != want:
        FAILED.append(name)
        print("FAIL %s\n   got  %r\n   want %r" % (name, got, want))
    else:
        print("ok   %s" % name)


def raises(name, fn, fragment):
    try:
        fn()
    except FormError as exc:
        check(name, fragment in str(exc), True)
        return
    except Exception as exc:                     # noqa: BLE001 - the type is the point
        check(name + " (wrong exception: %s)" % type(exc).__name__, False, True)
        return
    check(name + " (nothing raised)", False, True)


def q(qid, label, kind="text", page=0, **extra):
    item = {"id": qid, "label": label, "type": kind, "page": page}
    item.update(extra)
    return item


TRAVEL = {
    "kind": "survey",
    "questions": [
        q("travel", "How do you like to travel?", "radio", options=["Beach", "City"]),
        q("beach", "Favourite beach?", "text"),
        q("city", "Favourite city?", "text"),
        q("budget", "Budget per trip", "number", page=1),
    ],
}

# Answering "City" skips the beach question entirely.
TRAVEL["questions"][0]["logic"] = [{"when": {"q": "travel", "op": "eq", "value": "city"},
                                    "goto": "city"}]


def test_walk():
    check("no answers yet asks everything in order",
          forms_engine.walk(TRAVEL), ["travel", "beach", "city", "budget"])
    check("a branch skips the question it bypasses",
          forms_engine.walk(TRAVEL, {"travel": "City"}), ["travel", "city", "budget"])
    check("the other branch keeps it",
          forms_engine.walk(TRAVEL, {"travel": "beach"}), ["travel", "beach", "city", "budget"])
    check("visible questions drop the hidden one",
          [x["id"] for x in forms_engine.visible_questions(TRAVEL, {"travel": "city"})],
          ["travel", "city", "budget"])


def test_pages_and_end():
    form = {"kind": "form", "questions": [
        q("a", "A", "text", page=0),
        q("b", "B", "text", page=0),
        q("c", "C", "text", page=1),
        q("d", "D", "text", page=2),
    ]}
    form["questions"][0]["logic"] = [{"when": {"q": "a", "op": "eq", "value": "skip"},
                                      "goto": "page:2"}]
    check("a page jump lands on the first question of that page",
          forms_engine.walk(form, {"a": "skip"}), ["a", "d"])
    form["questions"][0]["logic"] = [{"when": {"q": "a", "op": "is_set"}, "goto": "end"}]
    check("goto end finishes the form", forms_engine.walk(form, {"a": "anything"}), ["a"])
    check("pages group the questions the visitor meets",
          [[x["id"] for x in group] for group in forms_engine.page_order(TRAVEL)],
          [["travel", "beach", "city"], ["budget"]])


def test_backwards_rule_cannot_loop():
    form = {"kind": "form", "questions": [q("a", "A"), q("b", "B")]}
    form["questions"][0]["logic"] = [{"when": {"q": "a", "op": "is_set"}, "goto": "b"}]
    form["questions"][1]["logic"] = [{"when": {"q": "b", "op": "is_set"}, "goto": "a"}]
    check("a rule pointing backwards stops instead of asking twice",
          forms_engine.walk(form, {"a": "x", "b": "y"}), ["a", "b"])



QUIZ = {
    "kind": "quiz",
    "questions": [
        q("start", "Which do you prefer?", "radio", options=["Sea", "Mountains"]),
        q("sea", "Capital of Portugal?", "radio", options=["Lisbon", "Porto"], required=True,
          score={"points": 2, "correct": "Lisbon"}),
        q("mountains", "Highest peak in Europe?", "radio", required=True,
          options=["Mont Blanc", "Elbrus"], score={"points": 2, "correct": "Elbrus"}),
        q("always", "Thanks for playing", "text"),
    ],
    "settings": {"outcomes": [
        {"min": 0, "label": "try-again", "message": "Have another go."},
        {"min": 100, "label": "perfect", "message": "Perfect score - book a call."},
    ]},
}
# Answering either branch question skips the other one entirely, which is what makes the
# marking honest: a perfect run is out of two, not out of four.
QUIZ["questions"][0]["logic"] = [{"when": {"q": "start", "op": "eq", "value": "mountains"},
                                  "goto": "mountains"},
                                 {"when": {"q": "start", "op": "eq", "value": "sea"},
                                  "goto": "sea"}]
QUIZ["questions"][1]["logic"] = [{"when": {"q": "sea", "op": "is_set"}, "goto": "always"}]
QUIZ["questions"][2]["logic"] = [{"when": {"q": "mountains", "op": "is_set"},
                                  "goto": "always"}]


def test_scoring_counts_only_what_was_asked():
    sea = forms_engine.score(QUIZ, {"start": "Sea", "sea": "Lisbon", "always": "thanks"})
    check("a correct branch scores, and the hidden question is not counted",
          (sea["score"], sea["max_score"], sea["percent"]), (2, 2, 100))
    wrong = forms_engine.score(QUIZ, {"start": "Mountains", "mountains": "Mont Blanc"})
    check("a wrong branch scores zero, still out of two",
          (wrong["score"], wrong["max_score"], wrong["percent"]), (0, 2, 0))
    check("a perfect run finds its bucket",
          forms_engine.outcome(QUIZ, sea)["label"], "perfect")
    check("a poor run finds the floor bucket",
          forms_engine.outcome(QUIZ, wrong)["label"], "try-again")
    check("no buckets means no outcome", forms_engine.outcome({"settings": {}}, sea), None)


def test_required_only_applies_to_visible_questions():
    form = {"kind": "form", "questions": [
        q("pick", "Pick", "radio", options=["a", "b"]),
        q("extra", "Only if b", "text", required=True),
    ]}
    form["questions"][0]["logic"] = [{"when": {"q": "pick", "op": "eq", "value": "a"},
                                      "goto": "end"}]
    check("a hidden required question is not required",
          forms_engine.validate_answers(form, {"pick": "a"}), [])
    check("a visible required question is",
          forms_engine.validate_answers(form, {"pick": "b"}),
          ["Only if b is required."])


def test_answer_types():
    form = {"kind": "form", "questions": [
        q("mail", "Email", "email", required=True),
        q("age", "Age", "number"),
        q("size", "Size", "rating", max=5),
        q("how", "How", "select", options=["One", "Two"]),
    ]}
    check("a bad email is caught",
          forms_engine.validate_answers(form, {"mail": "nope"}),
          ["Email needs an email address."])
    check("a good email passes", forms_engine.validate_answers(form, {"mail": "a@b.co"}), [])
    check("a non-number is caught",
          forms_engine.validate_answers(form, {"mail": "a@b.co", "age": "old"}),
          ["Age needs a number."])
    check("a rating outside its range is caught",
          forms_engine.validate_answers(form, {"mail": "a@b.co", "size": 9}),
          ["Size must be between 0 and 5."])
    check("an answer that is not an option is caught",
          forms_engine.validate_answers(form, {"mail": "a@b.co", "how": "Three"}),
          ["How has an answer that is not one of the options."])


def test_definition_is_validated():
    raises("a jump to nowhere is refused",
           lambda: forms_engine.validate({"kind": "form", "questions": [
               q("a", "A", logic=[{"when": {"q": "a", "op": "is_set"}, "goto": "ghost"}])]}),
           "does not exist")
    raises("a choice with no options is refused",
           lambda: forms_engine.validate({"kind": "form", "questions": [q("a", "A", "radio")]}),
           "no options")
    raises("duplicate ids are refused",
           lambda: forms_engine.validate({"kind": "form",
                                          "questions": [q("a", "A"), q("a", "B")]}),
           "share an id")
    raises("an unknown kind is refused",
           lambda: forms_engine.validate({"kind": "poll", "questions": [q("a", "A")]}),
           "Unknown kind")
    raises("an unknown test is refused",
           lambda: forms_engine.validate({"kind": "form", "questions": [
               q("a", "A", logic=[{"when": {"q": "a", "op": "matches"}, "goto": "end"}])]}),
           "unknown test")
    check("a valid form passes", forms_engine.validate(QUIZ), True)


def test_contact_capture():
    import db
    import forms
    from db import get_connection

    db.init_db()
    org = 990019
    with get_connection() as conn:
        for table in ("form_responses", "forms", "contacts"):
            conn.execute("DELETE FROM %s WHERE org_id = ?" % table, (org,))

    form = forms.create_form({"name": "Contact us", "questions": [
        q("full_name", "Your name", "text", required=True),
        q("mail", "Email", "email", required=True),
        q("message", "Message", "textarea"),
    ]}, org)
    form = forms.update_form(form["id"], org, {"status": "published"})
    result = forms.submit(form, {"full_name": "Ada Lovelace", "mail": "ada@example.test",
                                 "message": "hello"})
    check("a form asking for a name and an email creates a contact",
          bool(result["contact_id"]), True)
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM contacts WHERE id = ?",
                           (result["contact_id"],)).fetchone()
        conn.execute("DELETE FROM form_responses WHERE org_id = ?", (org,))
        conn.execute("DELETE FROM forms WHERE org_id = ?", (org,))
        conn.execute("DELETE FROM contacts WHERE org_id = ?", (org,))
    check("the contact carries what was typed",
          (row["name"], row["email"]), ("Ada Lovelace", "ada@example.test"))
    check("it is marked as coming from a form", row["source"], "form")


def test_the_agent_can_build_a_form():
    """The loop the whole feature exists for: a sentence in, a live quiz out."""
    import asyncio

    import db
    import jobs
    import tools
    from autogen_core import CancellationToken

    db.init_db()
    org = 990022
    with db.get_connection() as conn:
        for table in ("form_responses", "forms"):
            conn.execute("DELETE FROM %s WHERE org_id = ?" % table, (org,))
    job_id = jobs.create_job("form", "make a quiz", None, "selector", "chat", org_id=org)
    create = tools.resolve(["create_form"], job_id=job_id)[0]
    said = asyncio.run(create.run_json({
        "name": "Trip quiz", "kind": "quiz",
        "questions": [
            {"label": "Sea or mountains?", "type": "radio", "required": True,
             "options": ["Sea", "Mountains"],
             "logic": [{"when": {"q": "q1", "op": "is_set"}, "goto": "end"}]},
            {"label": "Favourite sea town?", "type": "radio", "options": ["Lisbon", "Porto"],
             "score": {"points": 2, "correct": "Lisbon"}},
        ]}, CancellationToken()))
    check("the agent can create a form", "Created the quiz" in said, True)
    check("and is told the link", "/f/trip-quiz" in said, True)

    import json
    listing = asyncio.run(tools.resolve(["list_forms"], job_id=job_id)[0].run_json(
        {}, CancellationToken()))
    rows = json.loads(listing)
    check("the agent can see what it built", rows[0]["questions"], 2)
    check("and that it is a draft", rows[0]["status"], "draft")

    check("without a client it refuses",
          asyncio.run(tools.resolve(["create_form"], job_id=None)[0].run_json(
              {"name": "x"}, CancellationToken())),
          "Pick a client first - a form belongs to a client.")
    with db.get_connection() as conn:
        for table in ("form_responses", "forms"):
            conn.execute("DELETE FROM %s WHERE org_id = ?" % table, (org,))
    import db
    import forms

    db.init_db()          # the local database predates these tables
    from db import get_connection

    org = 990018
    with get_connection() as conn:
        for table in ("form_responses", "forms", "contacts"):
            conn.execute("DELETE FROM %s WHERE org_id = ?" % table, (org,))

    created = forms.create_form({"name": "Italy quiz", "kind": "quiz",
                                 "questions": QUIZ["questions"],
                                 "settings": QUIZ["settings"]}, org, owner_id=None)
    check("create returns a slug", created["slug"], "italy-quiz")
    check("a new form starts as a draft", created["status"], "draft")
    check("the question count is right", created["question_count"], 4)
    check("one form is listed", len(forms.list_forms(org)), 1)

    published = forms.update_form(created["id"], org, {"status": "published"})
    check("publishing sticks", published["status"], "published")
    check("a published form is reachable by slug",
          (forms.get_by_slug("italy-quiz") or {}).get("id"), created["id"])

    result = forms.submit(published, {"start": "Sea", "sea": "Lisbon", "always": "thanks"})
    check("a submission is scored", result["score"]["percent"], 100)
    check("the outcome label is stored", result["outcome"]["label"], "perfect")
    check("an email-less quiz creates no contact", result["contact_id"], None)

    rows = forms.list_responses(created["id"], org)
    check("the response is stored", len(rows), 1)
    check("its answers came back as a dict", rows[0]["answers"]["sea"], "Lisbon")
    check("the csv has a column per question",
          forms.responses_csv(published, rows).splitlines()[0]
          .startswith("submitted_at,score,max_score,outcome"), True)

    refused = ""
    try:
        forms.submit(published, {"start": "Sea"})
    except forms.FormServiceError as exc:
        refused = str(exc)
    check("an incomplete submission is refused by the server, not the browser",
          "Capital of Portugal? is required." in refused, True)

    saved_invalid = True
    try:
        forms.create_form({"name": "Broken", "questions": [q("a", "A", "radio")]}, org)
    except forms.FormServiceError:
        saved_invalid = False
    check("an invalid form cannot be saved", saved_invalid, False)

    removed = forms.delete_form(created["id"], org)
    check("a form with answers is archived, not deleted", removed["archived"], True)
    check("archiving keeps the answers", len(forms.list_responses(created["id"], org)), 1)

    empty = forms.create_form({"name": "Empty", "questions": [q("a", "A")]}, org)
    check("an unanswered form is deleted outright",
          forms.delete_form(empty["id"], org), {"archived": False, "responses": 0})

    with get_connection() as conn:
        for table in ("form_responses", "forms", "contacts"):
            conn.execute("DELETE FROM %s WHERE org_id = ?" % table, (org,))


if __name__ == "__main__":
    test_walk()
    test_pages_and_end()
    test_backwards_rule_cannot_loop()
    test_scoring_counts_only_what_was_asked()
    test_required_only_applies_to_visible_questions()
    test_answer_types()
    test_definition_is_validated()
    test_contact_capture()
    test_the_agent_can_build_a_form()
    test_service_round_trip()
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nFORM ENGINE OK - branching, scoring and storage agree with each other")
