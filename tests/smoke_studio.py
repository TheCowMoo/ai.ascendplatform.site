"""The Studio: one draft on the canvas, context on every turn that edits it.

Run: .\\venv\\Scripts\\python.exe tests\\smoke_studio.py

The canvas is only real if the server can be told *which* artifact the user is looking
at, and only safe if that answer is checked against the session's client rather than
believed. This suite pins:

* a draft from another client is a 404, and no job is created;
* the Manager prompt names the draft, carries its current content, and says
  ``deliverable_id=`` - the tool argument the update path already understands;
* nothing the browser sends besides kind and id reaches the prompt;
* the voice delegation carries the same block, inside the 4000 characters the spoken
  follow-up copies;
* a job's drafts can be found by job id, scoped to the caller;
* ``start_job`` hands the draft id to the team, so delegated work edits the open page
  instead of saving a duplicate beside it.
"""

import asyncio
import os
import re
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.pop("ALLOW_LIVE_ACTIONS", None)

from autogen_core import CancellationToken  # noqa: E402

import app as appmod  # noqa: E402
import db  # noqa: E402
import deliverables  # noqa: E402
import jobs  # noqa: E402
import llm  # noqa: E402
import orgs as orgs_service  # noqa: E402
import tools as tool_registry  # noqa: E402

DRY = CancellationToken()
TOKEN = uuid.uuid4().hex[:6]
FAILED = []


def check(label, got, want):
    if got == want:
        print("  ok - %s" % label)
        return
    FAILED.append(label)
    print("  FAILED - %s: %r != %r" % (label, got, want))


def _org(label):
    """A client with its own login, so tenancy is exercised as a client, not only as us."""
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    email = "%s-%s@studio.test" % (slug, TOKEN)
    org = orgs_service.create_org({
        "name": "%s %s" % (label, TOKEN),
        "email": email,
        "password": "studio-pass-1234",
    })
    # get_org returns the org row; the login is its own table, so carry it here.
    org["email"] = email
    return org


def _login(org):
    client = appmod.app.test_client()
    resp = client.post("/api/auth/login",
                       json={"email": org["email"], "password": "studio-pass-1234"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return client


def _owner_client():
    client = appmod.app.test_client()
    resp = client.post("/api/auth/login",
                       json={"email": "admin@example.com", "password": "admin123"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return client


def _draft(org_id, title, content, job_id=None):
    return deliverables.create_deliverable({
        "kind": "website", "title": title, "content": content, "job_id": job_id,
    }, org_id=org_id)


def _stub_prompt(captured):
    """Capture the prompt the route builds, without running a job or calling a model."""
    def fake_create(title, prompt, requester=None, team_mode="selector",
                    task_name="echo", org_id=None, requester_id=None,
                    deliverable_id=None, read_only=False):
        captured["title"] = title
        captured["prompt"] = prompt
        captured["org"] = org_id
        captured["task"] = task_name
        captured["requester"] = requester if requester is not None else requester_id
        captured["deliverable_id"] = deliverable_id
        captured["read_only"] = read_only
        return 424242

    return fake_create


def _with_stubs(body):
    original_create, original_submit = jobs.create_job, jobs.submit
    original_providers = llm.configured_providers
    captured = {}
    jobs.create_job = _stub_prompt(captured)
    jobs.submit = lambda job_id: captured.setdefault("submitted", job_id)
    llm.configured_providers = lambda: ["deepseek"]
    try:
        return body(captured)
    finally:
        jobs.create_job, jobs.submit = original_create, original_submit
        llm.configured_providers = original_providers


def test_an_artifact_is_validated_against_the_session():
    alpha, beta = _org("Studio Alpha"), _org("Studio Beta")
    theirs = _draft(beta["id"], "Beta page %s" % TOKEN,
                    "<html><body>Beta secret</body></html>")
    mine = _draft(alpha["id"], "Alpha page %s" % TOKEN,
                  "<html><body>Alpha content</body></html>")

    def body(captured):
        client = _login(alpha)
        stolen = client.post("/api/manager/chat", json={
            "message": "Make it shorter",
            "artifact": {"kind": "deliverable", "id": theirs["id"]},
        })
        check("another client's draft is a 404", stolen.status_code, 404)
        check("and no job was created for it", captured.get("prompt"), None)

        for bad, why in (
            ({"kind": "site", "id": 1}, "an unknown artifact kind"),
            ({"kind": "deliverable"}, "a missing id"),
            ({"kind": "deliverable", "id": "abc"}, "a non-numeric id"),
            ("42", "a bare id"),
        ):
            refused = client.post("/api/manager/chat",
                                  json={"message": "Change it", "artifact": bad})
            check("%s is refused" % why, refused.status_code, 400)

        # The operator may read any client's draft, but not pull it into another
        # client's job. Acting as alpha, beta's draft is still out of reach.
        owner = _owner_client()
        owner.post("/api/session/org", json={"org_id": alpha["id"]})
        hijack = owner.post("/api/manager/chat", json={
            "message": "Make it shorter",
            "artifact": {"kind": "deliverable", "id": theirs["id"]},
        })
        check("the operator acting as alpha cannot name beta's draft",
              hijack.status_code, 404)

        # With no client picked there is nothing to attach a job to.
        overview = _owner_client()
        orphan = overview.post("/api/manager/chat", json={
            "message": "Make it shorter",
            "artifact": {"kind": "deliverable", "id": mine["id"]},
        })
        check("no client picked is a 400 with a reason", orphan.status_code, 400)
        check("and it says which switch to flip",
              "workspace switcher" in (orphan.get_json() or {}).get("error", ""), True)

        own = client.post("/api/manager/chat", json={
            "message": "Make it shorter",
            "artifact": {"kind": "deliverable", "id": mine["id"]},
        })
        check("the client's own draft is accepted", own.status_code, 201)

    _with_stubs(body)

    deliverables.delete_deliverable(theirs["id"])
    deliverables.delete_deliverable(mine["id"])
    print("artifact ids are checked against the session, not trusted")


def test_the_manager_prompt_names_the_draft():
    alpha = _org("Studio Prompt")
    content = "<html><body><h1>Bakery</h1><p>Real content</p></body></html>"
    mine = _draft(alpha["id"], "Bakery landing page %s" % TOKEN, content)

    def body(captured):
        client = _login(alpha)
        resp = client.post("/api/manager/chat", json={
            "message": "Make the headline shorter",
            "artifact": {"kind": "deliverable", "id": mine["id"],
                         "title": "Hijacked title", "content": "EVIL BLOB"},
        })
        check("the turn is accepted", resp.status_code, 201)
        prompt = captured.get("prompt") or ""
        check("the prompt opens with the Studio block",
              prompt.startswith("[Studio context"), True)
        check("it names the draft by id",
              ("deliverable #%d" % mine["id"]) in prompt, True)
        check("it names the draft by title",
              ("Bakery landing page %s" % TOKEN) in prompt, True)
        check("it carries the stored content", "Real content" in prompt, True)
        check("it tells the manager which id to save",
              ("deliverable_id=%d" % mine["id"]) in prompt, True)
        check("it forbids a duplicate", "Do not create a new draft" in prompt, True)
        check("the browser's content is never used", "EVIL BLOB" in prompt, False)
        check("the browser's title is never used", "Hijacked title" in prompt, False)
        check("the user's words come after the context",
              prompt.index("Make the headline shorter") > prompt.index("[Studio context"),
              True)
        check("the job belongs to the acting client", captured.get("org"), alpha["id"])

    _with_stubs(body)

    # Without an artifact nothing changes: an ordinary Assistant turn gains no block.
    def plain(captured):
        client = _login(alpha)
        client.post("/api/manager/chat", json={"message": "Hello there"})
        check("a plain turn has no Studio block",
              captured.get("prompt"), "Hello there")

    _with_stubs(plain)
    deliverables.delete_deliverable(mine["id"])
    print("the prompt carries the draft and ignores anything else the browser sends")


def test_the_voice_delegation_carries_the_draft():
    alpha = _org("Studio Voice")
    long_content = "<html><body>" + ("x" * 3000) + "</body></html>"
    mine = _draft(alpha["id"], "Bakery landing page %s" % TOKEN, long_content)

    def body(captured):
        client = _login(alpha)
        resp = client.post("/api/voice/tool", json={
            "name": "delegate_to_team",
            "turn_id": "turn-%s" % TOKEN,
            "args": {"instruction": "make it warmer"},
            "artifact": {"kind": "deliverable", "id": mine["id"]},
        })
        check("the spoken delegation is accepted", resp.status_code, 202)
        prompt = captured.get("prompt") or ""
        check("the prompt opens with the Studio block",
              prompt.startswith("[Studio context"), True)
        check("it names the id", ("deliverable_id=%d" % mine["id"]) in prompt, True)
        check("the instruction survives", "make it warmer" in prompt, True)
        check("it ends with the instruction", prompt.endswith("make it warmer"), True)
        # answer_team_question copies the previous prompt with a 4000-character cap.
        # If the block is longer than that, the follow-up loses the content it needs
        # to make the edit, so this is the contract, not a coincidence.
        closing = prompt.find("]")
        check("the whole block fits the spoken follow-up cap", 0 < closing < 4000, True)
        check("and it is truncated rather than cut mid-tag",
              "truncated - the document continues" in prompt, True)

        stolen = client.post("/api/voice/tool", json={
            "name": "delegate_to_team",
            "turn_id": "turn-bad-%s" % TOKEN,
            "args": {"instruction": "make it warmer"},
            "artifact": {"kind": "deliverable", "id": 999999999},
        })
        check("a draft that is not yours is a 404", stolen.status_code, 404)

    _with_stubs(body)
    deliverables.delete_deliverable(mine["id"])
    print("the voice delegation edits the draft on screen, inside the follow-up cap")


def test_outputs_can_be_found_by_job():
    alpha, beta = _org("Studio Outputs"), _org("Studio Outputs Other")
    first_job = jobs.create_job("Studio run one", "build a page", None, "selector",
                                "agent_team", org_id=alpha["id"])
    second_job = jobs.create_job("Studio run two", "build another page", None, "selector",
                                 "agent_team", org_id=alpha["id"])
    one = _draft(alpha["id"], "From run one %s" % TOKEN, "<html>one</html>", first_job)
    two = _draft(alpha["id"], "From run two %s" % TOKEN, "<html>two</html>", second_job)

    client = _login(alpha)
    listed = client.get("/api/deliverables?job_id=%d" % first_job).get_json()
    ids = [item["id"] for item in listed["deliverables"]]
    check("only the job's own draft comes back", ids, [one["id"]])
    check("the other run's draft does not", two["id"] in ids, False)

    bad = client.get("/api/deliverables?job_id=not-a-number")
    check("a non-numeric job id is refused", bad.status_code, 400)

    other = _login(beta)
    check("another client cannot read this job's drafts",
          other.get("/api/deliverables?job_id=%d" % first_job).get_json()["deliverables"],
          [])

    with db.get_connection() as conn:
        conn.execute("DELETE FROM deliverables WHERE id IN (?, ?)", (one["id"], two["id"]))
        conn.execute("DELETE FROM job_events WHERE job_id IN (?, ?)", (first_job, second_job))
        conn.execute("DELETE FROM jobs WHERE id IN (?, ?)", (first_job, second_job))
    print("a run's drafts are discoverable by job id, inside the caller's client")


def test_start_job_hands_over_the_draft_id():
    alpha = _org("Studio Delegate")
    parent = jobs.create_job("Studio parent", "[Studio context ...]", None, "selector",
                             "chat", org_id=alpha["id"])
    tool = tool_registry.resolve(["start_job"], job_id=parent)[0]
    captured = {}

    original_create, original_submit = jobs.create_job, jobs.submit
    jobs.create_job = _stub_prompt(captured)
    jobs.submit = lambda job_id: captured.setdefault("submitted", job_id)
    try:
        reply = asyncio.run(tool.run_json({
            "prompt": "shorten the headline", "deliverable_id": 4242,
        }, DRY))
    finally:
        jobs.create_job, jobs.submit = original_create, original_submit

    check("the team was started", captured.get("submitted"), 424242)
    check("its prompt names the draft",
          "deliverable_id=4242" in (captured.get("prompt") or ""), True)
    check("and keeps the instruction first",
          (captured.get("prompt") or "").startswith("shorten the headline"), True)
    print("start_job(delegated) reply:", reply)

    with db.get_connection() as conn:
        conn.execute("DELETE FROM job_events WHERE job_id = ?", (parent,))
        conn.execute("DELETE FROM jobs WHERE id = ?", (parent,))


def test_a_bound_job_edits_without_being_told():
    """The Studio binds the run to the draft; the model need not repeat the id.

    This is the duplicate-draft path: the prompt asks the model to pass deliverable_id,
    but an agent that forgets used to save a sibling with the same title.
    """
    org = _org("Studio Bound Save")
    draft = _draft(org["id"], "Landing %s" % TOKEN, "<p>first</p>")
    job_id = jobs.create_job("Studio edit", "make it warmer", None, "selector", "chat",
                             org_id=org["id"], deliverable_id=draft["id"])
    tool = tool_registry.resolve(["save_deliverable"], job_id=job_id)[0]
    reply = asyncio.run(tool.run_json(
        {"kind": "website", "title": "A model title", "content": "<p>second</p>"}, DRY))
    rows = deliverables.list_deliverables("website", org["id"])
    check("the bound draft was updated, not duplicated", len(rows), 1)
    check("and it is the same row", rows[0]["id"], draft["id"])
    check("with the new content", "<p>second</p>" in rows[0]["content"], True)
    check("the reply says updated", reply.startswith("Updated"), True)
    check("the title is the user's, not the model's", rows[0]["title"],
          "Landing %s" % TOKEN)
    check("the writing job is recorded on the row", rows[0]["job_id"], job_id)
    with db.get_connection() as conn:
        conn.execute("DELETE FROM job_events WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    print("a job bound to a draft edits it even when the model passes no id")


def test_a_delegated_job_inherits_the_bound_draft():
    org = _org("Studio Inherit")
    draft = _draft(org["id"], "Inherit %s" % TOKEN, "<p>x</p>")
    parent = jobs.create_job("Studio parent", "[Studio context ...]", None, "selector",
                             "chat", org_id=org["id"], deliverable_id=draft["id"])
    tool = tool_registry.resolve(["start_job"], job_id=parent)[0]
    captured = {}
    original_create, original_submit = jobs.create_job, jobs.submit
    jobs.create_job = _stub_prompt(captured)
    jobs.submit = lambda job_id: captured.setdefault("submitted", job_id)
    try:
        asyncio.run(tool.run_json({"prompt": "shorten the headline"}, DRY))
    finally:
        jobs.create_job, jobs.submit = original_create, original_submit
    check("the child inherits the parent's draft",
          captured.get("deliverable_id"), draft["id"])
    check("and its prompt names it too",
          ("deliverable_id=%d" % draft["id"]) in (captured.get("prompt") or ""), True)
    with db.get_connection() as conn:
        conn.execute("DELETE FROM job_events WHERE job_id = ?", (parent,))
        conn.execute("DELETE FROM jobs WHERE id = ?", (parent,))
    print("a delegated edit inherits the draft its parent job was bound to")


def test_an_edit_is_discoverable_by_its_job():
    """The canvas finds a draft by ?job_id=; an update must count as that job's output."""
    org = _org("Studio Edit By Job")
    client = _login(org)
    draft = _draft(org["id"], "Edited %s" % TOKEN, "<p>old</p>")
    job_id = jobs.create_job("Studio edit", "warmer", None, "selector", "chat",
                             org_id=org["id"], deliverable_id=draft["id"])
    tool = tool_registry.resolve(["save_deliverable"], job_id=job_id)[0]
    asyncio.run(tool.run_json(
        {"kind": "website", "title": "x", "content": "<p>new</p>"}, DRY))
    listed = client.get("/api/deliverables?job_id=%d" % job_id).get_json()["deliverables"]
    check("the edited draft is found by the job that wrote it",
          [row["id"] for row in listed], [draft["id"]])
    with db.get_connection() as conn:
        conn.execute("DELETE FROM job_events WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    print("an in-place edit is discoverable by job id, so the preview can follow it")


def test_a_chat_remembers_the_draft():
    """A Studio session that edited #42 reopens on #42, and cannot name another client's."""
    org = _org("Studio Chat Draft")
    client = _login(org)
    mine = _draft(org["id"], "Remember %s" % TOKEN, "<p>page</p>")
    other = _org("Studio Chat Other")
    theirs = _draft(other["id"], "Theirs %s" % TOKEN, "<p>theirs</p>")
    resp = _with_stubs(lambda held: client.post("/api/manager/chat", json={
        "message": "make it warmer", "create_session": True,
        "artifact": {"kind": "deliverable", "id": mine["id"]},
    }))
    body = resp.get_json()
    check("the turn is accepted", resp.status_code, 201)
    check("the job is bound to the draft", body.get("deliverable_id"), mine["id"])
    session = client.get("/api/assistant/sessions/%d" % body["session_id"]).get_json()
    check("the chat remembers the draft", session["session"]["deliverable_id"], mine["id"])

    switched = client.post("/api/assistant/sessions/%d/draft" % body["session_id"],
                           json={"deliverable_id": mine["id"]})
    check("the canvas can be re-pointed at an owned draft", switched.status_code, 200)
    stolen = client.post("/api/assistant/sessions/%d/draft" % body["session_id"],
                         json={"deliverable_id": theirs["id"]})
    check("another client's draft is a 404", stolen.status_code, 404)
    cleared = client.post("/api/assistant/sessions/%d/draft" % body["session_id"],
                          json={"deliverable_id": None})
    check("null clears the canvas memory", cleared.get_json()["deliverable_id"], None)
    print("a chat remembers which draft it is about, inside its own client")


def test_the_studio_page_is_wired():
    """Static: the server can be perfect and the canvas still never open.

    The Studio spans four files, and a missing piece is invisible until someone clicks
    the tab, so each link in the chain is named here.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def read(*parts):
        return open(os.path.join(root, *parts), encoding="utf-8").read()

    admin_js = read("admin", "admin.js")
    voice_js = read("admin", "voice.js")
    page = read("admin", "index.html")
    css = read("admin", "admin.css")

    check("the nav has a Studio tab", "{ id: 'studio'" in admin_js, True)
    check("the router sends /studio to its view", "view = viewStudio(" in admin_js, True)
    check("typed turns carry the artifact", "payload.artifact = artifact" in admin_js, True)
    check("voice is docked with the canvas context",
          "getContext: artifactRef" in admin_js, True)
    check("a job's drafts are found by job id",
          "'/api/deliverables?job_id='" in admin_js, True)
    check("the canvas preview is sandboxed",
          "studio-preview-frame" in admin_js and
          "setAttribute('sandbox', '')" in admin_js, True)
    check("in-page switches do not re-render the shell",
          "history.replaceState(null, '', '#/app/studio/deliverable/'" in admin_js, True)
    check("the nav icon exists", 'id="i-studio"' in page, True)
    check("the panes are styled", ".studio-layout" in css and ".studio-canvas" in css, True)
    check("the docked panel is styled", ".voice-docked" in css, True)
    check("the voice reads the canvas when it delegates", "getContext" in voice_js, True)

    # The merge: one tool, with the chats inside it.
    check("the Studio is the workspace landing", "home: '#/app/studio'" in admin_js, True)
    check("the Assistant tab is gone", "{ id: 'assistant'" in admin_js, False)
    check("an old Assistant link lands in the Studio",
          "location.replace('#/app/studio')" in admin_js, True)
    check("the Studio has a chat rail",
          "studio-rail" in admin_js and ".studio-rail" in css, True)
    check("a chat can be started from the Studio",
          "function startNewChat" in admin_js and
          "newChat.onclick = startNewChat" in admin_js, True)
    check("a new chat blanks the canvas", "clearArtifact();" in admin_js, True)
    check("opening a chat restores its draft",
          "found.deliverable_id" in admin_js, True)
    check("the canvas remembers its chat's draft", "/draft'" in admin_js, True)
    check("the open draft is re-read while a run is live",
          "if (activeArtifact && anyRunning()) refreshArtifact();" in admin_js, True)
    check("the rail becomes a strip before the panes squeeze",
          "grid-template-areas: \"rail rail\"" in css, True)
    check("the draft is remembered outside the view",
          "studio:draft:" in admin_js and "writeStudioDraft(activeArtifact)" in admin_js,
          True)
    check("the voice falls back to the remembered draft",
          "return readStudioDraft();" in admin_js, True)
    check("a reload restores it", "var remembered = readStudioDraft();" in admin_js, True)
    # The operator works for many clients from one login. Keying the chat and draft by the
    # login rather than the acting client put every workspace in one slot.
    check("the memory is scoped to the acting client",
          "function studioOrgKey()" in admin_js and "var orgKey = studioOrgKey();" in admin_js,
          True)
    check("switching workspace updates that scope",
          "state.user.acting_org = chosen" in admin_js, True)
    check("a chat with no draft does not clear the canvas on mount",
          "keepCanvasIfEmpty: true" in admin_js, True)
    check("a remembered chat that is gone falls back to the newest",
          "var newest = (list || [])[0];" in admin_js, True)
    check("opening a draft links it to the chat",
          "if (activeArtifact && changed) rememberDraft();" in admin_js, True)
    # Kept across tabs: the node is moved, not rebuilt, and a strip is the door back.
    check("the Studio survives a tab switch",
          "function keepStudio(" in admin_js and "function restoreStudio(" in admin_js, True)
    check("the kept node has a hidden holder", "studio-kept" in admin_js, True)
    check("a strip brings it back",
          "studio-strip" in admin_js and ".studio-strip" in css, True)
    check("the strip shows the voice state", "phase-' + phase" in admin_js, True)
    check("the orb yields to the kept Studio",
          "studioKept.node && hash.indexOf" in admin_js, True)
    check("the sidebar is grouped", "id: 'sites-group'" in admin_js, True)
    check("a group opens in place",
          "side-group" in admin_js and ".side-group" in css, True)
    check("children are first-class leaves", "function flattenNav(" in admin_js, True)
    check("open groups are remembered", "nav:open:" in admin_js, True)
    print("the Studio tab, the canvas and the docked voice are wired together")


def cleanup():
    """Remove every fixture this run created, even after a failure."""
    with db.get_connection() as conn:
        orgs = conn.execute(
            "SELECT id FROM orgs WHERE name LIKE ?", ("%%Studio%%%s" % TOKEN,)
        ).fetchall()
        for org in orgs:
            org_id = org["id"]
            conn.execute(
                "DELETE FROM assistant_messages WHERE session_id IN "
                "(SELECT id FROM assistant_sessions WHERE org_id = ?)", (org_id,))
            for table in ("assistant_sessions", "deliverables", "sites", "business_profiles",
                          "client_settings"):
                try:
                    conn.execute("DELETE FROM %s WHERE org_id = ?" % table, (org_id,))
                except Exception:
                    pass        # a table this build does not have yet
            conn.execute("DELETE FROM job_events WHERE job_id IN "
                         "(SELECT id FROM jobs WHERE org_id = ?)", (org_id,))
            conn.execute("DELETE FROM jobs WHERE org_id = ?", (org_id,))
            conn.execute("DELETE FROM users WHERE org_id = ?", (org_id,))
            conn.execute("DELETE FROM orgs WHERE id = ?", (org_id,))


def main():
    try:
        test_an_artifact_is_validated_against_the_session()
        test_the_manager_prompt_names_the_draft()
        test_the_voice_delegation_carries_the_draft()
        test_outputs_can_be_found_by_job()
        test_start_job_hands_over_the_draft_id()
        test_a_bound_job_edits_without_being_told()
        test_a_delegated_job_inherits_the_bound_draft()
        test_an_edit_is_discoverable_by_its_job()
        test_a_chat_remembers_the_draft()
        test_the_studio_page_is_wired()
    finally:
        cleanup()
    if FAILED:
        print("\n%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
        raise SystemExit(1)
    print("\nSTUDIO OK - the canvas names a draft the server has checked")


if __name__ == "__main__":
    main()
