"""The form engine: what a visitor is asked next, what is required, and what it scores.

No database, no HTTP. The same rules have to hold in three places - the hosted page deciding
which question to show, the submit endpoint deciding what is valid, and the tests deciding
whether those two agree - so they live once, here.

A branching form is a walk, not a tree: every question may carry rules of the form
``{when: {q, op, value}, goto: "<question id>" | "page:<n>" | "end"}``. The first matching
rule decides where the visitor goes after answering. The walk keeps a visited set, so a rule
that points backwards ends the form instead of asking the same question forever - a public
URL must not be able to hang a request on a mis-typed rule.
"""

KINDS = ("form", "survey", "quiz")
STATUSES = ("draft", "published", "archived")
QUESTION_TYPES = ("text", "email", "phone", "textarea", "number", "date",
                  "select", "radio", "checkbox", "rating")
CHOICE_TYPES = ("select", "radio", "checkbox")
OPS = ("eq", "ne", "contains", "gt", "lt", "is_set", "is_empty")
MAX_HOPS = 200


class FormError(ValueError):
    pass


def normalise(value):
    """A comparable form of an answer: trimmed, lowered, lists sorted.

    Everything an answer can be - a string, a number, a list of ticked boxes - becomes
    something two dictionaries in different processes can agree on.
    """
    if isinstance(value, (list, tuple)):
        return sorted(str(normalise(item)) for item in value if str(item).strip())
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value if value is not None else "").strip().lower()


def question_list(form):
    """The form's questions, in order, ignoring anything malformed."""
    out = []
    for item in ((form or {}).get("questions") or []):
        if isinstance(item, dict) and str(item.get("id") or "").strip():
            out.append(item)
    return out


def options_of(question):
    """``[{"value", "label"}]`` for a choice question, accepting plain strings too."""
    out = []
    for item in (question.get("options") or []):
        if isinstance(item, dict):
            value = str(item.get("value", item.get("label", "")) or "").strip()
            label = str(item.get("label", value) or value).strip()
        else:
            value = label = str(item or "").strip()
        if value:
            out.append({"value": value, "label": label})
    return out


def page_of(question):
    try:
        return max(0, int(question.get("page") or 0))
    except (TypeError, ValueError):
        return 0


def points_of(question):
    cfg = question.get("score")
    if isinstance(cfg, dict):
        try:
            return max(0, int(cfg.get("points") or 0))
        except (TypeError, ValueError):
            return 0
    return 0


def rule_matches(rule, answers):
    """Whether one branching rule fires, given the answers so far."""
    when = (rule or {}).get("when") or {}
    op = str(when.get("op") or "eq").strip().lower()
    if op not in OPS:
        return False
    got = normalise((answers or {}).get(when.get("q")))
    want = normalise(when.get("value"))
    if op == "is_set":
        return bool(got)
    if op == "is_empty":
        return not got
    if op == "eq":
        return got == want
    if op == "ne":
        return got != want
    if op == "contains":
        if not want:
            return False
        if isinstance(got, list):
            return want in got
        return want in got
    # gt / lt: numeric when both sides are numbers, alphabetical otherwise. A rating of 4 is
    # "greater than 3" as a number and also as a string, so this only matters for mixed input.
    try:
        left, right = float(got), float(want)
    except (TypeError, ValueError):
        left, right = str(got), str(want)
    return left > right if op == "gt" else left < right


def next_target(question, answers):
    """Where the first matching rule sends the visitor, or None to simply continue."""
    for rule in (question.get("logic") or []):
        if not isinstance(rule, dict):
            continue
        if rule_matches(rule, answers):
            target = str(rule.get("goto") or "").strip()
            return target or None
    return None

def walk(form, answers=None, limit=MAX_HOPS):
    """The question ids a visitor is asked, in order, given the answers so far."""
    questions = question_list(form)
    order = [q["id"] for q in questions]
    by_id = {q["id"]: q for q in questions}
    shown, visited = [], set()
    index, hops = 0, 0
    while 0 <= index < len(order):
        hops += 1
        if hops > limit:
            raise FormError("The branching rules loop - no visitor could finish this form.")
        qid = order[index]
        if qid in visited:
            break          # a rule pointed backwards; stop rather than ask the same thing twice
        visited.add(qid)
        shown.append(qid)
        target = next_target(by_id[qid], answers)
        if not target:
            index += 1
            continue
        if target == "end":
            break
        if target.startswith("page:"):
            try:
                page = int(target.split(":", 1)[1])
            except ValueError:
                index += 1
                continue
            found = -1
            for pos, other in enumerate(order):
                if page_of(by_id[other]) == page:
                    found = pos
                    break
            index = found if found >= 0 else index + 1
            continue
        if target in by_id:
            index = order.index(target)
            continue
        index += 1         # an unknown target is skipped, so a half-edited form still runs
    return shown


def visible_questions(form, answers=None):
    by_id = {q["id"]: q for q in question_list(form)}
    return [by_id[qid] for qid in walk(form, answers) if qid in by_id]


def page_order(form):
    """Questions grouped by page, in the order the visitor meets them."""
    pages = {}
    for question in question_list(form):
        pages.setdefault(page_of(question), []).append(question)
    return [pages[key] for key in sorted(pages)]


def score(form, answers=None):
    """Marks, counted only over the questions this visitor was actually asked.

    A hidden question cannot contribute: otherwise branching a quiz in two directions would
    cap a perfect run at half marks.
    """
    earned = total = correct = scored = 0
    for question in visible_questions(form, answers):
        points = points_of(question)
        if points <= 0:
            continue
        scored += 1
        total += points
        cfg = question.get("score") or {}
        want = cfg.get("correct")
        if want not in (None, "", []):
            if normalise((answers or {}).get(question["id"])) == normalise(want):
                earned += points
                correct += 1
    percent = int(round(100.0 * earned / total)) if total else 0
    return {"score": earned, "max_score": total, "correct": correct,
            "scored": scored, "percent": percent}


def outcome(form, result):
    """The message bucket a score falls into: the highest ``min`` at or below the percentage."""
    buckets = []
    for item in ((form or {}).get("settings") or {}).get("outcomes") or []:
        if not isinstance(item, dict):
            continue
        try:
            minimum = int(item.get("min") or 0)
        except (TypeError, ValueError):
            minimum = 0
        buckets.append((minimum, item))
    percent = int((result or {}).get("percent") or 0)
    chosen = None
    for minimum, item in sorted(buckets, key=lambda pair: pair[0]):
        if percent >= minimum:
            chosen = item
    if not chosen:
        return None
    return {"label": str(chosen.get("label") or "").strip(),
            "message": str(chosen.get("message") or "").strip()}


def validate_answers(form, answers):
    """Everything wrong with a set of answers, as sentences. An empty list means fine."""
    problems = []
    for question in visible_questions(form, answers):
        value = (answers or {}).get(question["id"])
        value = value if value is not None else ""
        label = str(question.get("label") or question.get("id") or "A question").strip()
        if not normalise(value):
            if question.get("required"):
                problems.append("%s is required." % label)
            continue
        kind = str(question.get("type") or "text").strip().lower()
        allowed = normalise([opt["value"] for opt in options_of(question)])
        if kind == "email":
            text = normalise(value)
            if "@" not in text or "." not in text.split("@")[-1]:
                problems.append("%s needs an email address." % label)
        elif kind == "number":
            try:
                float(normalise(value))
            except (TypeError, ValueError):
                problems.append("%s needs a number." % label)
        elif kind == "rating":
            try:
                top = int(question.get("max") or 5)
                number = float(normalise(value))
            except (TypeError, ValueError):
                problems.append("%s needs a rating." % label)
            else:
                if number < 0 or number > top:
                    problems.append("%s must be between 0 and %d." % (label, top))
        elif kind in CHOICE_TYPES:
            given = normalise(value)
            given = given if isinstance(given, list) else [given]
            if allowed and any(item not in allowed for item in given):
                problems.append("%s has an answer that is not one of the options." % label)
    return problems


def validate(form):
    """Everything wrong with the *definition*. Raises FormError on the first problem."""
    kind = str((form or {}).get("kind") or "form").strip().lower()
    if kind not in KINDS:
        raise FormError("Unknown kind: %s" % kind)
    questions = question_list(form)
    if not questions:
        raise FormError("A form needs at least one question.")
    ids = [q["id"] for q in questions]
    if len(set(ids)) != len(ids):
        raise FormError("Two questions share an id - branching rules could not tell them apart.")
    known = set(ids)
    page_count = max([page_of(q) for q in questions]) + 1
    for question in questions:
        kind_of = str(question.get("type") or "text").strip().lower()
        if kind_of not in QUESTION_TYPES:
            raise FormError("Question %s has an unknown type: %s" % (question["id"], kind_of))
        if not str(question.get("label") or "").strip():
            raise FormError("Question %s needs a label." % question["id"])
        if kind_of in CHOICE_TYPES and not options_of(question):
            raise FormError("Question %s is a choice but has no options." % question["id"])
        for rule in (question.get("logic") or []):
            if not isinstance(rule, dict):
                raise FormError("Question %s has a branching rule that is not a rule."
                                % question["id"])
            when = rule.get("when") or {}
            if str(when.get("op") or "eq").strip().lower() not in OPS:
                raise FormError("Question %s branches on an unknown test: %s"
                                % (question["id"], when.get("op")))
            if when.get("q") and when.get("q") not in known:
                raise FormError("Question %s branches on a question that does not exist: %s"
                                % (question["id"], when.get("q")))
            target = str(rule.get("goto") or "").strip()
            if not target:
                raise FormError("Question %s has a rule with nowhere to go." % question["id"])
            if target == "end":
                continue
            if target.startswith("page:"):
                try:
                    page = int(target.split(":", 1)[1])
                except ValueError:
                    raise FormError("Question %s jumps to a page that does not parse."
                                    % question["id"])
                if page < 0 or page >= page_count:
                    raise FormError("Question %s jumps to page %d, which does not exist."
                                    % (question["id"], page))
                continue
            if target not in known:
                raise FormError("Question %s jumps to %s, which does not exist."
                                % (question["id"], target))
    return True
