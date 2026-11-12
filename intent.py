"""Does this request ask to change something, or only to look at it?

The distinction is the first line of defence against a review job overwriting the page
it was asked to inspect. A model can phrase a read-only turn many ways, so this is
deliberately conservative: it only says "read-only" for explicit no-write wording, a
"read-only" label, or a review verb applied to a page/site with no edit verb. An edit
request that happens to contain "review" ("review it and fix the typos") stays an edit.

It is used at the entry points that create a job, so the job row carries the answer.
The team build and the write tools then enforce it; the wording is not the guard.
"""

import re

# Strong no-write wording. The gap allows "do not redesign or republish", "without
# changing anything", "never touch the live page".
_NO_WRITE = re.compile(
    r"\b(?:do not|don't|without|never)\b"
    r"[^.\n]{0,80}\b(?:change|edit|modify|save|publish|republish|overwrite|redesign|"
    r"touch|update|write|build|create)\b",
    re.I,
)
_READ_ONLY = re.compile(r"\bread[- ]only\b", re.I)
_QA = re.compile(r"\b(?:qa|quality assurance)\b", re.I)
_REVIEW = re.compile(r"\b(?:verify|review|audit|inspect|check)\b", re.I)
_SUBJECT = re.compile(
    r"\b(?:page|site|website|url|domain|live|draft|link|form|homepage)\b", re.I
)
_EDIT = re.compile(
    r"\b(?:fix|change|edit|update|rewrite|make|add|remove|replace|publish|republish|"
    r"build|create|redesign|shorten|improve|rewrite)\b",
    re.I,
)


def looks_read_only(text):
    """True when the request should be a read-only review.

    A false positive is annoying but safe: the user can send a second, explicit change
    request. A false negative is dangerous, so the strong no-write wording wins even
    when an edit verb appears elsewhere in the sentence.
    """
    value = str(text or "")
    if not value.strip():
        return False
    if _READ_ONLY.search(value) or _NO_WRITE.search(value):
        return True
    if _QA.search(value) and _REVIEW.search(value):
        return True
    if _REVIEW.search(value) and _SUBJECT.search(value) and not _EDIT.search(value):
        return True
    return False
