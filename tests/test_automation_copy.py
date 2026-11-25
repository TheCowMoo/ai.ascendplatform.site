"""The Automations screen speaks to a client, not to a developer.

A screen that says "form.submitted", "graph" or "awaiting_approval" asks the client to
learn the implementation. These checks read the source (there is no browser in preflight)
and pin the plain wording and the one-approval flow.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = open(os.path.join(ROOT, "admin", "admin.js"), encoding="utf-8").read()
CSS = open(os.path.join(ROOT, "admin", "admin.css"), encoding="utf-8").read()

FAILED = []


def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        FAILED.append(name)


def main():
    # The developer words are gone from the Automations screen.
    check("no graph jargon in the header",
          "A named graph of steps" not in JS)
    check("no raw event names in the trigger field",
          "contact.created or form.submitted" not in JS)
    check("no old draft-queue heading",
          "Drafts from the team" not in JS)
    check("no old approval wording",
          "press Apply" not in JS)

    # One approval is the action.
    check("the draft card approves and turns on in one click",
          "Approve & turn on" in JS)
    check("the editor's draft banner uses the same approval",
          "A change is waiting for your approval" in JS)
    check("the screen says what approval does",
          "will now run by itself" in JS)
    check("the run button is a test, not an operator command",
          "Try it now" in JS)

    # The trigger picker is a picker, with real forms.
    check("the trigger asks when it should run",
          "When should it run?" in JS)
    check("a form is chosen by name",
          "When someone sends a form" in JS and "api('GET', '/api/forms')" in JS)
    check("the other triggers are plain too",
          "When a new contact is added" in JS and "Only when I press Try it now" in JS)
    check("the advanced fields are behind a toggle",
          "Advanced settings" in JS)

    # Step types and run states are translated.
    check("step types have plain labels", "automationStepLabel" in JS and
          "gate: 'Approval'" in JS)
    check("run states have plain labels", "automationRunLabel" in JS and
          "Waiting for your approval" in JS)
    check("the graph no longer shouts internal types",
          "String(step.type || '').toUpperCase()" not in JS)

    # The technical controls stay out of the default editor.
    check("technical fields are hidden until Advanced",
          ".auto-steps:not(.show-advanced) .auto-cond { display: none; }" in CSS)

    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nAUTOMATION COPY OK - one approval, plain words, forms by name")


if __name__ == "__main__":
    main()
