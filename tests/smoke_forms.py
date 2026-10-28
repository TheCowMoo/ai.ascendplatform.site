"""The hosted form page and its two public endpoints.

A public URL is the one surface in this app anybody can reach, so these tests attack it the
way the internet will: an unpublished draft must not answer, a honeypot must swallow bots
silently, a missing required answer must be refused by the *server*, and a quiz's correct
answers must never be in the HTML.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.pop("ALLOW_LIVE_ACTIONS", None)

import app as appmod  # noqa: E402
import db  # noqa: E402
import forms  # noqa: E402

TOKEN = str(os.getpid())
FAILED = []


def check(name, got, want=True, *ignored):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name
          + ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


BOOKING = {
    "name": "Trip planner",
    "kind": "quiz",
    "pages": [{"title": "About you"}, {"title": "Sea"}, {"title": "Mountains"},
              {"title": "Done"}],
    "questions": [
        {"id": "start", "type": "radio", "label": "Which do you prefer?", "page": 0,
         "required": True, "options": ["Sea", "Mountains"],
         "logic": [{"when": {"q": "start", "op": "eq", "value": "sea"}, "goto": "sea"},
                   {"when": {"q": "start", "op": "eq", "value": "mountains"},
                    "goto": "mountains"}]},
        {"id": "sea", "type": "radio", "label": "Best sea town?", "page": 1, "required": True,
         "options": ["Lisbon", "Porto"], "score": {"points": 2, "correct": "Lisbon"},
         "logic": [{"when": {"q": "sea", "op": "is_set"}, "goto": "always"}]},
        {"id": "mountains", "type": "radio", "label": "Highest peak?", "page": 2,
         "required": True, "options": ["Mont Blanc", "Elbrus"],
         "score": {"points": 2, "correct": "Elbrus"},
         "logic": [{"when": {"q": "mountains", "op": "is_set"}, "goto": "always"}]},
        {"id": "always", "type": "text", "label": "Anything else?", "page": 3},
    ],
    "settings": {"success": "Thanks - your trips are on the way.",
                 "outcomes": [{"min": 0, "label": "try-again",
                               "message": "Have another go."},
                              {"min": 100, "label": "perfect",
                               "message": "Perfect - we will call."}]},
}


def cleanup(org):
    with db.get_connection() as conn:
        for table in ("form_responses", "forms", "contacts"):
            conn.execute("DELETE FROM %s WHERE org_id = ?" % table, (org,))


def main():
    db.init_db()
    org = 990020
    cleanup(org)
    published = forms.create_form(BOOKING, org)
    published = forms.update_form(published["id"], org, {"status": "published"})
    draft = forms.create_form(dict(BOOKING, name="Draft %s" % TOKEN,
                                   slug="draft-%s" % TOKEN), org)
    slug, draft_slug = published["slug"], draft["slug"]

    client = appmod.app.test_client()

    # --- the page ------------------------------------------------------------
    resp = client.get("/f/%s" % slug)
    body = resp.get_data(as_text=True)
    check("a published form opens to anyone", resp.status_code, 200)
    check("it is served as HTML", resp.headers["Content-Type"].startswith("text/html"))
    check("the name is on the page", "Trip planner" in body)
    check("the first question is on the page", "Which do you prefer?" in body)
    check("the scoring rules are not on the page", "correct" not in body.lower())
    check("the page locks itself down",
          "default-src 'none'" in resp.headers["Content-Security-Policy"])

    # The page's JavaScript is 6KB of untested code unless something parses it. Node is not
    # guaranteed on a server, so this check skips rather than failing when it is missing.
    import re
    import shutil
    import subprocess
    import tempfile

    script = re.search(r"<script>(.*)</script>", body, re.S)
    if shutil.which("node") and script:
        path = os.path.join(tempfile.gettempdir(), "form_page_%s.js" % TOKEN)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(script.group(1))
        result = subprocess.run(["node", "--check", path], capture_output=True, text=True)
        check("the page's JavaScript parses", result.returncode, 0)
        check("and the step and submit URLs are baked in",
              ('"step": "/api/public/forms/%s/step"' % slug) in body, True)

    resp = client.get("/f/%s" % draft_slug)
    check("an unpublished form answers nobody", resp.status_code, 404)
    resp = client.get("/f/%s?preview=1" % draft_slug)
    check("a preview needs a login", resp.status_code, 404)
    resp = client.get("/f/made-up-%s" % TOKEN)
    check("an unknown slug is a 404", resp.status_code, 404)

    # --- stepping ------------------------------------------------------------
    resp = client.post("/api/public/forms/%s/step" % slug, json={"answers": {"start": "Sea"}})
    data = resp.get_json()
    check("the server sends the next page", [q["id"] for q in data["questions"]], ["sea"])
    check("progress is reported", data["progress"]["step"], 1)

    resp = client.post("/api/public/forms/%s/step" % slug,
                       json={"answers": {"start": "Mountains"}})
    check("the other branch is sent instead",
          [q["id"] for q in resp.get_json()["questions"]], ["mountains"])

    resp = client.post("/api/public/forms/%s/step" % slug,
                       json={"answers": {"start": "Sea", "sea": "Lisbon"}})
    check("answering the branch jumps past the other one",
          [q["id"] for q in resp.get_json()["questions"]], ["always"])

    # --- submitting ----------------------------------------------------------
    resp = client.post("/api/public/forms/%s" % slug, json={"answers": {"start": "Sea"}})
    check("an incomplete submission is refused by the server", resp.status_code, 400)
    check("and says which question",
          (resp.get_json() or {}).get("error"), "Best sea town? is required.")

    resp = client.post("/api/public/forms/%s" % slug,
                       json={"answers": {"start": "Sea", "sea": "Lisbon", "always": "hi"}})
    data = resp.get_json() or {}
    check("a complete submission is accepted", resp.status_code, 201, resp.status_code)
    check("a perfect quiz says so", data.get("message"), "Perfect - we will call.")
    check("the score is returned", (data.get("score") or {}).get("percent"), 100)

    rows = forms.list_responses(published["id"], org)
    check("the answer is stored", len(rows), 1)
    check("the score is stored with it", (rows[0]["score"], rows[0]["max_score"]), (2, 2))
    check("the visitor's IP is hashed, not kept", len(rows[0]["ip_hash"]), 32)
    check("no contact is invented for a quiz", rows[0]["contact_id"], None)

    resp = client.post("/api/public/forms/%s" % slug,
                       json={"answers": {"start": "Sea", "sea": "Lisbon"}, "company": "bot"})
    check("a honeypot hit is answered as if it worked", resp.status_code, 202)
    check("and nothing is stored", len(forms.list_responses(published["id"], org)), 1)

    # --- preview -------------------------------------------------------------
    resp = client.post("/api/auth/login",
                       json={"email": "admin@example.com", "password": "admin123"})
    check("the operator can log in", resp.status_code, 200)
    orgs = client.get("/api/orgs").get_json().get("orgs") or []
    mine = [o for o in orgs if o["id"] == org]
    if mine:
        client.post("/api/session/org", json={"org_id": org})
        resp = client.get("/f/%s?preview=1" % draft_slug)
        check("a logged-in owner can preview their own draft", resp.status_code, 200,
              resp.status_code)
        resp = client.post("/api/public/forms/%s?preview=1" % draft_slug,
                           json={"answers": {"start": "Mountains", "mountains": "Elbrus"}})
        check("a preview submission is accepted", resp.status_code, 201, resp.status_code)
        check("a preview is scored for real",
              (resp.get_json().get("score") or {}).get("percent"), 100)
        check("but nothing is written", len(forms.list_responses(draft["id"], org)), 0)
    else:
        print("skip preview checks: no org %s for the operator to act as" % org)

    cleanup(org)
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nFORM PAGES OK - the public side refuses what it should and stores what it should")


if __name__ == "__main__":
    main()
