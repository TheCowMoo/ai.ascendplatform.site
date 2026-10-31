"""Tools for the floating manager: read the platform, edit drafts, delegate work.

Two rules baked in. Ownership comes from the **job row**, never from the model's
wording, so it cannot be talked into another client's data. And nothing outward-facing
is granted here - sending and publishing stay behind the team's own gated tools, because
a chat turn that blocks for five minutes on an approval is a worse experience than a job
you can watch.
"""

import json

import deliverables as deliverables_service
import sites as sites_service
from db import get_connection

from . import register


def _context(job_id):
    """(org_id, requester_id) for the job the tool call belongs to."""
    if not job_id:
        return None, None
    with get_connection() as conn:
        row = conn.execute(
            "SELECT org_id, requester_id FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
    if not row:
        return None, None
    return row["org_id"], row["requester_id"]


def _build_list_sites(emit, job_id=None):
    async def list_sites() -> str:
        """List this client's websites with status and URL."""
        org_id, _ = _context(job_id)
        rows = sites_service.list_sites(org_id)
        if not rows:
            return "No sites yet for this client."
        return json.dumps(
            [{"domain": r["domain"], "status": r["status"], "url": r["url"]} for r in rows],
            indent=1,
        )

    return list_sites


def _build_list_deliverables(emit, job_id=None):
    async def list_deliverables_of_kind(kind: str = "") -> str:
        """List recent drafts, optionally filtered to one kind (email, blog, website...)."""
        org_id, _ = _context(job_id)
        rows = deliverables_service.list_deliverables(kind.strip().lower() or None, org_id)
        if not rows:
            return "No drafts yet."
        return json.dumps(
            [{"id": r["id"], "kind": r["kind"], "title": r["title"], "status": r["status"]}
             for r in rows[:15]],
            indent=1,
        )

    return list_deliverables_of_kind


def _build_get_metrics(emit, job_id=None):
    async def get_metrics(days: int = 7) -> str:
        """This client's traffic for the last N days, against the window before it."""
        import review

        org_id, _ = _context(job_id)
        if org_id is None:
            return "This chat is not attached to a client, so there are no metrics."
        brief = review.brief(org_id, days=max(1, min(90, int(days or 7))))
        return json.dumps(brief, indent=1)

    return get_metrics


def _build_start_job(emit, job_id=None):
    async def start_job(prompt: str, title: str = "") -> str:
        """Hand work to the agent team: writing, reviewing a site, building a page."""
        import jobs

        if not (prompt or "").strip():
            return "A prompt is required."
        org_id, requester = _context(job_id)
        if org_id is None:
            return (
                "I can't start work that belongs to no client - it would never show up "
                "in their Jobs or Library. Pick a client in the workspace switcher, then "
                "ask me again."
            )
        new_id = jobs.create_job(
            title.strip() or prompt.strip()[:60],
            prompt.strip(),
            requester_id=requester,
            team_mode="selector",
            task_name="agent_team",
            org_id=org_id,
        )
        jobs.submit(new_id)      # a created job that is never submitted queues forever
        title_text = title.strip() or prompt.strip()[:60]
        if emit:
            # The voice and the console must be able to follow the work the manager handed
            # to the team. The parent transcript is the durable link: no job schema change,
            # and a later viewer can see exactly which background run was started here.
            emit("Manager", "child_job", json.dumps(
                {"job_id": new_id, "title": title_text}))
        return "Started job #%d. Open it in Jobs to follow along." % new_id

    return start_job


def _build_propose_automation(emit, job_id=None):
    async def propose_automation(name: str, steps: str, automation_id: int = 0) -> str:
        """Draft an automation graph for the client to review, then apply.

        ``steps`` is a JSON array of node objects, each with ``id``, ``type``, ``title``
        and ``next`` (plus ``config`` for an agent or condition node) - the tool schema
        needs one type per argument, so a JSON string is what the model sends.

        Nothing becomes live: the draft goes into the proposals queue and a human presses
        Apply. The validator's complaints come back verbatim - fix them and call again
        with the whole graph, because a half-corrected graph is a different graph.
        """
        import json

        import automations

        org_id, requester = _context(job_id)
        if org_id is None:
            return ("I can't draft an automation without a client. Pick one in the "
                    "workspace switcher, then ask again.")
        if isinstance(steps, str):
            try:
                steps = json.loads(steps)
            except ValueError as exc:
                return ("Those steps are not valid JSON (%s). Pass a list of node objects, "
                        "each with id, type, title and next." % exc)
        if not isinstance(steps, list) or not steps:
            return ("Pass steps as a list of node objects: id, type, title, next, and "
                    "config for an agent or condition node.")
        # A tool name is otherwise only checked when the run reaches that step - after a
        # human approved the graph and a real contact was waiting on the email. Check it
        # while the model can still fix it.
        import tools as tool_registry

        unknown = []
        for step in steps:
            if not isinstance(step, dict) or step.get("type") != "tool":
                continue
            wanted = str(((step.get("config") or {}).get("tool") or "")).strip()
            if wanted and not tool_registry.resolve([wanted], job_id=job_id):
                unknown.append(wanted)
        if unknown:
            return ("Not saved: there is no tool called %s. Call describe_capabilities to "
                    "see the tools this client has." % ", ".join(sorted(set(unknown))))
        draft, problems = automations.propose(
            org_id, name, steps, automation_id=int(automation_id) or None,
            created_by=requester, job_id=job_id,
        )
        if problems:
            return ("Not saved. Fix these and call again with the complete graph: "
                    + " ".join(problems))
        return ("Drafted it as proposal #%d - %d steps. It is not live: open Automations, "
                "preview it, and press Apply to save it."
                % (draft["id"], len(draft["steps"])))

    return propose_automation


register("propose_automation",
         "Draft an automation graph for the client to preview and apply. Nothing goes live.",
         _build_propose_automation)


def _approved_tools(org_id):
    """Approved agent-written tools. Empty is the normal state: none can run yet."""
    try:
        import agent_tools

        return agent_tools.approved_tools(org_id)
    except Exception:
        return []


def _build_describe_capabilities(emit, job_id=None):
    async def describe_capabilities() -> str:
        """What this client can build automations from right now.

        Call this before drafting: it is the difference between composing from what
        exists and inventing a tool name the validator will refuse.
        """
        import json

        import automations
        import settings as settings_mod
        import templates_service

        org_id, _ = _context(job_id)
        if org_id is None:
            return "Pick a client first - capabilities are per client."
        return json.dumps({
            "node_types": list(automations.NODE_TYPES),
            "triggers": {
                "manual": "run by hand",
                "schedule": {"every_minutes": 1440, "note": "minutes between runs"},
                "event": ["contact.created"],
            },
            "tools": ["send_email", "tag_contact", "save_deliverable", "get_metrics",
                      "list_sites", "start_job"],
            "conditions": list(automations.CONDITION_FACTS),
            "delays": {"config": "days, hours or minutes", "max_days": 30,
                       "note": "a delay parks the run and wakes it; use it for drip pacing"},
            "can_create": {
                "template": "create_template(name, subject, body) - do this instead of "
                            "telling the user to make one",
                "contact": "create_contact(name, email, phone, notes)",
                "form": "create_form(name, kind, questions) - a form, survey or quiz at its "
                        "own link, and it triggers form.submitted when answered",
                "tag": "tag_contact(contact_id, tags) inside a graph",
                "draft": "save_deliverable(kind, title, content)",
                "automation": "propose_automation(name, steps) - a draft the user applies",
                "never": ["SMTP settings", "API keys", "the domain a site lives on"],
            },
            "templates": [{"id": t["id"], "name": t["name"]}
                          for t in templates_service.list_templates(org_id)],
            "email_ready": bool(settings_mod.get("SMTP_HOST", org_id=org_id)),
            "stock_photos": {
                "configured": bool(settings_mod.get("PEXELS_API_KEY")),
                "flow": "search_photos -> import_photo_to_media -> use the returned media URL/HTML",
                "requires_approval": False,
                "note": ("Use this instead of stopping when list_media is empty. The photo "
                         "is stored in this client's Media."),
            },
            "agent_tools": [tool["name"] for tool in _approved_tools(org_id)],
            "note": ("Outward tools (send_email, publish_site, provision_site) need a gate "
                     "node before them or the graph is refused. If what you need is not "
                     "listed, say so and call propose_tool - it is recorded for approval "
                     "and does not run."),
        }, indent=2)

    return describe_capabilities


def _build_tag_contact(emit, job_id=None):
    async def tag_contact(contact_id: str = "", tags: str = "") -> str:
        """Add tags to a contact. Internal, so no approval gate is needed.

        Used as a step inside an automation: ``{"contact_id": "{{contact.id}}",
        "tags": "welcome-sequence"}`` tags whoever triggered the run.
        """
        import contacts as contacts_service

        org_id, _ = _context(job_id)
        if org_id is None:
            return "This job has no client, so there is no contact to tag."
        try:
            wanted = [part.strip() for part in str(tags or "").split(",") if part.strip()]
        except Exception:
            wanted = []
        if not wanted:
            return "No tags given, so nothing changed."
        try:
            cid = int(str(contact_id).strip())
        except (TypeError, ValueError):
            return "contact_id must be a number; got %r." % contact_id
        contact = contacts_service.get_contact(cid, org_id)
        if not contact:
            return "No contact %s for this client." % cid
        merged = list(dict.fromkeys((contact.get("tags") or []) + wanted))
        contacts_service.update_contact(cid, org_id, {"tags": merged})
        return "Tagged contact %s: %s" % (cid, ", ".join(merged))

    return tag_contact


def _build_propose_tool(emit, job_id=None):
    async def propose_tool(name: str, description: str, kind: str = "recipe",
                           args_schema: str = "{}", body: str = "") -> str:
        """Ask for a capability that does not exist yet.

        The tool is written to a proposals table and is NOT usable: a human approves it
        first, and no executor exists yet either. Say what you need and why.
        """
        import agent_tools

        org_id, requester = _context(job_id)
        if org_id is None:
            return "Pick a client first - tools are per client."
        try:
            row = agent_tools.propose({"name": name, "description": description, "kind": kind,
                                       "args_schema": args_schema, "body": body},
                                      org_id, created_by=requester, job_id=job_id)
        except agent_tools.AgentToolError as exc:
            return "Not recorded: %s" % exc
        return ("Recorded %s as a proposed tool (#%d, %s). It cannot run yet: a human has to "
                "approve it, and this build has no executor for either kind."
                % (row["name"], row["id"], row["kind"]))

    return propose_tool


def _build_ask_user(emit, job_id=None):
    async def ask_user(questions: str) -> str:
        """Ask for the details that would change what you build.

        Pass a JSON list of at most three short questions. Nothing is built until the user
        answers, and their answers arrive in the next turn.
        """
        import json
        import re

        parsed = None
        if isinstance(questions, str):
            try:
                parsed = json.loads(questions)
            except ValueError:
                parsed = None
        else:
            parsed = questions
        def _split(items):
            """One question per line, whether the model wrapped them in JSON or not."""
            out = []
            for item in items or []:
                text = str(item)
                if "\n" in text or re.search(r"(^|\s)\d+[).]\s", text):
                    out.extend(part.strip(' []"\'') for part in
                               re.split(r"\r?\n+|\s*\d+[).]\s*", text) if part.strip(' []"\''))
                else:
                    out.append(text.strip())
            return [question for question in out if question]

        if parsed is None:
            # Models regularly send one string with numbered lines instead of a JSON list -
            # the transcript that exposed this had all three questions inside a single
            # element. A successful JSON parse can still hide that, so both go through the
            # same splitter.
            parsed = [str(questions or "")]
        if isinstance(parsed, str):
            parsed = [parsed]
        cleaned = _split(parsed)[:3]
        if not cleaned:
            return "No questions given, so nothing was asked."
        if emit:
            # Type "question" so the console can show these as a card rather than as text
            # that scrolls past in the transcript.
            emit("Manager", "question", json.dumps(cleaned))
        return ("Asked the user %d question(s). Wait for their answers in the next turn "
                "before starting any work." % len(cleaned))

    return ask_user


def _build_create_template(emit, job_id=None):
    async def create_template(name: str, subject: str, body: str) -> str:
        """Write an email template for this client. Returns its id for a send step.

        Create what the work needs rather than telling the user to. The body may use
        {{contact.name}} and {{contact.email}}, which are filled from the contact.
        """
        import templates_service

        org_id, _ = _context(job_id)
        if org_id is None:
            return "Pick a client first - templates belong to a client."
        try:
            row = templates_service.save_template(
                {"name": name, "subject": subject, "body": body}, org_id)
        except templates_service.TemplateError as exc:
            return "Not created: %s" % exc
        return ("Created template #%d “%s”. Use template_id %d in a send_email step."
                % (row["id"], row["name"], row["id"]))

    return create_template


def _build_list_templates(emit, job_id=None):
    async def list_templates() -> str:
        """Templates this client already has - reuse one instead of writing a duplicate."""
        import json

        import templates_service

        org_id, _ = _context(job_id)
        if org_id is None:
            return "Pick a client first."
        rows = templates_service.list_templates(org_id)
        if not rows:
            return "No templates yet."
        return json.dumps([{"id": row["id"], "name": row["name"], "subject": row["subject"]}
                           for row in rows])

    return list_templates


def _build_create_contact(emit, job_id=None):
    async def create_contact(name: str, email: str = "", phone: str = "",
                             notes: str = "") -> str:
        """Add somebody to this client's CRM.

        A contact created here fires contact.created like any other lead, so an automation
        waiting on signups will start. That is intended: a new lead is a new lead.
        """
        import contacts as contacts_service

        org_id, _ = _context(job_id)
        if org_id is None:
            return "Pick a client first - contacts belong to a client."
        try:
            row = contacts_service.create_contact(
                {"name": name, "email": email, "phone": phone, "notes": notes},
                org_id, source="manual")
        except contacts_service.ContactError as exc:
            return "Not created: %s" % exc
        return "Added contact #%d (%s)." % (row["id"], row["name"] or row["email"])

    return create_contact


def _build_create_site(emit, job_id=None):
    async def create_site(domain: str) -> str:
        """Add a website for this client.

        The domain is a required argument on purpose: it costs money and points at a real
        server, so it is the user's to give. Never invent one - ask.
        """
        import provisioner
        import sites as sites_service

        org_id, requester = _context(job_id)
        if org_id is None:
            return "Pick a client first - a site belongs to a client."
        name = (domain or "").strip().lower()
        if "." not in name or " " in name:
            return ("That is not a domain (%r). Ask the user for the exact one, then add it."
                    % domain)
        try:
            site = sites_service.create_site({"domain": name, "target": "local"},
                                            owner_id=requester, org_id=org_id)
        except Exception as exc:
            return "Not created: %s" % str(exc)[:200]
        ip = provisioner.server_ip() or "this server's IP"
        return ("Added %s (#%s), pending DNS. Point its A record at %s, then run "
                "Check again in Domains." % (name, (site or {}).get("id", "?"), ip))

    return create_site


register("create_site", "Add a website for this client; the domain must come from the user.",
         _build_create_site)


def _build_create_form(emit, job_id=None):
    async def create_form(name: str, kind: str = "form", questions: list = None) -> str:
        """Create a form, survey or quiz for this client and return its link.

        Each question is an object: {"id", "label", "type", "page", "required", "options",
        "score": {"points", "correct"}, "logic": [{"when": {"q", "op", "value"},
        "goto": "<question id>" | "page:<n>" | "end"}]}. Types are text, email, phone,
        textarea, number, date, select, radio, checkbox and rating. Give every choice a list
        of options; give a quiz question points and the correct answer, which must match an
        option. A rule is what makes a branch: after this answer, jump somewhere.
        """
        import forms as forms_service

        org_id, _ = _context(job_id)
        if org_id is None:
            return "Pick a client first - a form belongs to a client."
        rows = []
        for index, item in enumerate(questions or []):
            if not isinstance(item, dict):
                return "Not created: every question must be an object."
            row = dict(item)
            row.setdefault("id", "q%d" % (index + 1))
            row.setdefault("type", "text")
            row.setdefault("page", 0)
            rows.append(row)
        try:
            form = forms_service.create_form(
                {"name": name, "kind": (kind or "form").strip().lower(), "questions": rows},
                org_id)
        except forms_service.FormServiceError as exc:
            return "Not created: %s" % exc
        return ("Created the %s “%s” (#%d) as a draft at /f/%s. It collects answers as soon "
                "as it is published - ask me to publish it, or press Publish in Forms."
                % (form["kind"], form["name"], form["id"], form["slug"]))

    return create_form


def _build_list_forms(emit, job_id=None):
    async def list_forms() -> str:
        """Forms this client already has, with their links and how many answers arrived."""
        import json

        import forms as forms_service

        org_id, _ = _context(job_id)
        if org_id is None:
            return "Pick a client first."
        rows = forms_service.list_forms(org_id)
        if not rows:
            return "No forms yet."
        return json.dumps([{"id": row["id"], "name": row["name"], "kind": row["kind"],
                            "status": row["status"], "slug": row["slug"],
                            "questions": row["question_count"],
                            "responses": row["responses"]} for row in rows])

    return list_forms


register("create_form",
         "Create a form, survey or quiz for this client; returns the link it will live at.",
         _build_create_form)


register("list_forms", "List this client's forms, with their links and answer counts.",
         _build_list_forms)


register("create_contact", "Add a contact to this client's CRM.",
         _build_create_contact)


register("create_template",
         "Write an email template for this client; returns the id a send step needs.",
         _build_create_template)


register("list_templates", "List this client's email templates, so you reuse rather than duplicate.",
         _build_list_templates)


register("ask_user",
         "Ask up to three questions when the brief is missing something that matters.",
         _build_ask_user)


register("propose_tool",
         "Propose a new tool for this client (recorded, not executed until approved).",
         _build_propose_tool)


register("tag_contact", "Add tags to a contact, e.g. tags=\"welcome, sequence\".",
         _build_tag_contact)


register("describe_capabilities",
         "List the triggers, tools, conditions and templates this client has.",
         _build_describe_capabilities)


register("list_sites", "List this client's websites with status and URL.",         _build_list_sites)
register("list_deliverables",
         "List recent drafts, optionally filtered to one kind (email, blog, website).",
         _build_list_deliverables)
register("get_metrics",
         "This client's traffic for the last N days, compared with the window before it.",
         _build_get_metrics)
register("start_job",
         "Hand work to the agent team - writing, reviewing a site, building a page. "
         "Returns the new job id.",
         _build_start_job)
