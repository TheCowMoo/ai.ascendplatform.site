"""Evidence-backed next-step recommendations for a client workspace.

The engine has two layers:

* ``signals`` / ``candidates`` are deterministic. They read tenant-scoped counts,
  statuses and titles, never raw contact details, and attach the evidence behind every
  suggestion. They work with no AI key.
* ``refine`` optionally asks the configured model to rank and reword the candidates.
  It may not invent a key, metric or recommendation: unknown keys are dropped and the
  original evidence/action metadata stays on every card.

One cached batch per client is stored in ``recommendation_batches``. A fingerprint of
the signals makes it stale when something meaningful changes; the 12-hour cache keeps
a dashboard open from spending a model call on every page view.
"""

import datetime
import hashlib
import json
import re

from db import days_ago, get_connection, now_str

CACHE_HOURS = 12
AI_RETRY_HOURS = 1
AI_MIN_MINUTES = 1
MAX_CARDS = 5
MAX_CANDIDATES = 10
STATUSES = ("open", "dismissed", "done")


def _cutoff(hours):
    return (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(hours=max(0, hours))).strftime("%Y-%m-%d %H:%M:%S")


def _load_row(org_id):
    if org_id is None:
        return None
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM recommendation_batches WHERE org_id = ?", (org_id,)
        ).fetchone()


def _payload(row):
    if not row:
        return []
    try:
        value = json.loads(row["payload"] or "[]")
    except (TypeError, ValueError):
        value = []
    return [item for item in value if isinstance(item, dict) and item.get("key")]


def _fingerprint(signals):
    return hashlib.sha256(
        json.dumps(signals, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _count(conn, sql, params=()):
    row = conn.execute(sql, tuple(params)).fetchone()
    return int((row["n"] if row else 0) or 0)


def signals(org_id):
    """Client-safe facts used by recommendations. No contact PII, emails or HTML."""
    if org_id is None:
        return {}
    import analytics_alerts
    import bots as bots_service
    import deliverables as deliverables_service
    import forms as forms_service
    import profile as profile_mod
    import sites as sites_service
    import templates_service

    profile = profile_mod.get_profile(org_id) or {}
    sites = sites_service.list_sites(org_id, include_content=False)
    forms = forms_service.list_forms(org_id)
    pages = deliverables_service.list_deliverables(
        None, org_id, limit=200, include_content=False)
    bots = bots_service.list_bots(org_id)
    templates = templates_service.list_templates(org_id)
    with get_connection() as conn:
        contacts = {
            "total": _count(conn, "SELECT COUNT(*) AS n FROM contacts WHERE org_id = ?",
                            (org_id,)),
            "new_7d": _count(
                conn, "SELECT COUNT(*) AS n FROM contacts WHERE org_id = ? "
                      "AND created_at >= ?", (org_id, days_ago(7))),
        }
        media = _count(conn, "SELECT COUNT(*) AS n FROM media WHERE org_id = ?",
                       (org_id,))
        automations = {
            "total": _count(conn, "SELECT COUNT(*) AS n FROM automations WHERE org_id = ?",
                            (org_id,)),
            "enabled": _count(
                conn, "SELECT COUNT(*) AS n FROM automations WHERE org_id = ? "
                      "AND enabled = 1", (org_id,)),
        }
    try:
        insights = analytics_alerts.insights(org_id, days=7).get("insights") or []
    except Exception:
        insights = []
    try:
        alerts = analytics_alerts.list_alerts(org_id, status="open", limit=5)
    except Exception:
        alerts = []
    proposals = [item for item in pages if item.get("kind") == "proposal"]
    return {
        "profile": {
            "name": str(profile.get("name") or ""),
            "description": str(profile.get("description") or ""),
            "missing": [label for field, label in (
                ("name", "business name"), ("description", "what you do"),
                ("support_email", "support email"), ("target_audience", "who you help"),
            ) if not str(profile.get(field) or "").strip()],
        },
        "sites": [
            {"id": item.get("id"), "domain": item.get("domain") or "",
             "status": item.get("status") or "",
             "linked_bot_id": item.get("linked_bot_id") or "",
             "updated_at": item.get("updated_at") or ""}
            for item in sites
        ],
        "pages": [
            {"id": item.get("id"), "kind": item.get("kind") or "",
             "title": item.get("title") or "", "status": item.get("status") or "",
             "updated_at": item.get("updated_at") or ""}
            for item in pages
        ],
        "forms": [
            {"id": item.get("id"), "name": item.get("name") or "",
             "status": item.get("status") or "", "responses": item.get("responses") or 0}
            for item in forms
        ],
        "bots": [{"id": item.get("id"), "name": item.get("name") or ""} for item in bots],
        "templates": {"total": len(templates)},
        "media": {"total": media},
        "contacts": contacts,
        "automations": automations,
        "alerts": [
            {"id": item.get("id"), "title": item.get("title") or "",
             "detail": item.get("detail") or "", "severity": "warning"}
            for item in alerts
        ],
        "insights": [
            {"key": item.get("key") or "", "title": item.get("title") or "",
             "detail": item.get("detail") or "", "severity": item.get("severity") or "info"}
            for item in insights
        ],
        "proposals": [
            {"id": item.get("id"), "title": item.get("title") or "",
             "status": item.get("status") or "", "summary": item.get("summary") or ""}
            for item in proposals[:3]
        ],
    }


def _card(key, category, priority, title, why, action, href, evidence,
          audience="all"):
    return {
        "key": key[:80],
        "category": category[:40],
        "priority": int(priority),
        "title": " ".join(str(title or "").split())[:120],
        "why": " ".join(str(why or "").split())[:320],
        "action_text": " ".join(str(action or "").split())[:500],
        "ask_prompt": " ".join(str(action or "").split())[:500],
        "href": str(href or "")[:300],
        "evidence": [" ".join(str(item).split())[:180]
                     for item in (evidence or []) if str(item).strip()][:4],
        "audience": "admin" if audience == "admin" else "all",
        "source": "rules",
        "status": "open",
    }


def candidates(signals):
    """Deterministic recommendations with evidence, best first."""
    data = signals or {}
    out = []
    profile = data.get("profile") or {}
    missing = profile.get("missing") or []
    if missing:
        out.append(_card(
            "complete_profile", "Business details", 1,
            "Complete your business details",
            "The AI is missing %s, so content and answers stay generic."
            % ", ".join(missing),
            "Help me complete my business details: %s." % ", ".join(missing),
            "#/app/profile", ["Missing: " + ", ".join(missing)]))
    sites = data.get("sites") or []
    live = [item for item in sites if item.get("status") == "live"]
    pending = [item for item in sites
               if item.get("status") in ("pending_dns", "dns_ok")]
    broken = [item for item in sites if item.get("status") == "error"]
    if broken:
        out.append(_card(
            "fix_site", "Website", 1,
            "Fix the website for %s" % broken[0].get("domain", "your domain"),
            "The last provisioning attempt for %s failed." % broken[0].get("domain", "the site"),
            "Help me fix the website for %s." % broken[0].get("domain", "my domain"),
            "#/app/domains",
            ["Provisioning status: error"]))
    elif pending:
        out.append(_card(
            "finish_site", "Website", 1,
            "Finish connecting %s" % pending[0].get("domain", "your domain"),
            "The domain is saved but is not live yet.",
            "Check the DNS for %s and finish publishing the site." %
            pending[0].get("domain", "my domain"),
            "#/app/domains", ["Site status: %s" % pending[0].get("status")]))
    elif not sites:
        out.append(_card(
            "get_site_live", "Website", 2,
            "Get your website live",
            "No domain is connected yet, so there is nothing for visitors to open.",
            "Help me connect my domain and publish a first page.",
            "#/app/domains", ["No connected sites"]))
    drafts = [item for item in (data.get("pages") or [])
              if item.get("kind") == "website" and item.get("status") == "draft"]
    if drafts:
        item = drafts[0]
        out.append(_card(
            "publish_page", "Website", 2,
            "Review and publish %s" % item.get("title", "your draft"),
            "%d website page%s waiting as a draft." %
            (len(drafts), "" if len(drafts) == 1 else "s are"),
            "Review the draft %s and publish it when it is ready." %
            item.get("title", "page"),
            "#/app/studio/website/%s" % item.get("id"),
            ["%d draft page(s)" % len(drafts)]))

    forms = data.get("forms") or []
    published_forms = [item for item in forms if item.get("status") == "published"]
    draft_forms = [item for item in forms if item.get("status") == "draft"]
    answered = [item for item in forms if (item.get("responses") or 0) > 0]
    if not forms:
        out.append(_card(
            "add_form", "Leads", 3,
            "Add an enquiry form",
            "Visitors can read the site but have no simple way to send you a lead.",
            "Create a short enquiry form and link it from the home page.",
            "#/app/forms", ["No forms yet"]))
    elif draft_forms:
        item = draft_forms[0]
        out.append(_card(
            "publish_form", "Leads", 2,
            "Publish %s" % item.get("name", "your form"),
            "The form is saved but visitors cannot open a draft.",
            "Publish the form %s and link it from the site." % item.get("name", ""),
            "#/app/forms/%s" % item.get("id"),
            ["Form status: draft"]))
    elif published_forms and not answered:
        item = published_forms[0]
        out.append(_card(
            "share_form", "Leads", 3,
            "Put %s in front of visitors" % item.get("name", "your form"),
            "The form is live but has no answers yet.",
            "Add a clear call to action for the form %s on the site." %
            item.get("name", ""),
            "#/app/forms/%s" % item.get("id"), ["0 responses"]))
    if answered:
        total_answers = sum(item.get("responses") or 0 for item in answered)
        newest = max(answered, key=lambda item: item.get("responses") or 0)
        out.append(_card(
            "follow_up_leads", "Leads", 2,
            "Follow up with new enquiries",
            "%d answer%s arrived; the newest lead may still be waiting." %
            (total_answers, "" if total_answers == 1 else "s"),
            "Draft a short follow-up email to the newest enquiries.",
            "#/app/forms/%s/responses" % newest.get("id"),
            ["%d total response(s)" % total_answers]))
    bots = data.get("bots") or []
    linked = {site.get("linked_bot_id") for site in sites if site.get("linked_bot_id")}
    if live and not bots:
        out.append(_card(
            "add_chat", "Chat", 3,
            "Add a chat assistant to %s" % live[0].get("domain", "your site"),
            "Visitors can ask questions, but there is no assistant on the site.",
            "Create a chat assistant for %s and answer the most common questions." %
            live[0].get("domain", "my site"),
            "#/app/bots", ["No chat assistants"]))
    elif bots and not linked:
        out.append(_card(
            "attach_chat", "Chat", 2,
            "Put %s on your site" % bots[0].get("name", "your assistant"),
            "A chat assistant exists but is not attached to a live site.",
            "Attach %s to %s." % (bots[0].get("name", "the assistant"),
                                  live[0].get("domain", "my site") if live else "my site"),
            "#/app/bots", ["Assistant not linked to a site"]))
    if (data.get("templates") or {}).get("total", 0) == 0 and (
            forms or (data.get("contacts") or {}).get("total", 0)):
        out.append(_card(
            "welcome_email", "Email", 3,
            "Draft a welcome email",
            "A new lead has nowhere to receive a first follow-up yet.",
            "Draft a short welcome email template for new enquiries.",
            "#/app/emails", ["No email templates"]))
    if (data.get("automations") or {}).get("enabled", 0) == 0 and forms:
        out.append(_card(
            "automate_followup", "Automations", 2,
            "Automate the follow-up for new enquiries",
            "Form answers arrive, but nothing follows up automatically.",
            "Create a simple automation that follows up after an enquiry form is sent.",
            "#/app/studio", ["No enabled automations"]))

    alerts = data.get("alerts") or []
    if alerts:
        item = alerts[0]
        out.append(_card(
            "review_alert", "Analytics", 1,
            "Review: %s" % item.get("title", "an analytics alert"),
            item.get("detail", "A tracked metric moved outside its normal range."),
            "Look at the analytics alert %s and suggest one concrete change." %
            item.get("title", "for this site"),
            "#/app/analytics", ["Open analytics alert"], audience="admin"))
    else:
        for item in (data.get("insights") or []):
            if item.get("severity") not in ("critical", "warning"):
                continue
            out.append(_card(
                "review_insight", "Analytics", 1,
                "Act on: %s" % item.get("title", "a traffic change"),
                item.get("detail", "Traffic changed enough to review."),
                "Review the traffic change %s and suggest one concrete change." %
                item.get("title", "for this site"),
                "#/app/analytics", ["Insight severity: %s" % item.get("severity")],
                audience="admin"))
            break
    proposals = data.get("proposals") or []
    if proposals:
        out.append(_card(
            "read_review", "Content", 3,
            "Read the latest site review",
            "A recent review already contains proposed changes; choose one instead of starting from scratch.",
            "Summarise the latest site review and help me choose one change.",
            "#/app/library", ["Draft: %s" % proposals[0].get("title", "proposal")]))
    if not (data.get("media") or {}).get("total", 0) and live:
        out.append(_card(
            "add_media", "Website", 4,
            "Add photos to the site",
            "The Media library is empty, so pages have no client-owned images.",
            "Find and import suitable photos for the site.",
            "#/app/media", ["No uploaded media"]))
    content = [item for item in (data.get("pages") or [])
               if item.get("kind") in ("blog", "email", "funnel")]
    if live and not content:
        out.append(_card(
            "next_content", "Content", 4,
            "Plan the next piece of content",
            "The site is live, but there is no blog post, email or landing page yet.",
            "Suggest one useful blog post for this business and draft it.",
            "#/app/studio", ["No content drafts"]))
    out.sort(key=lambda item: (item.get("priority", 9), item.get("key", "")))
    return out[:MAX_CANDIDATES]


def _preserve(old_cards, new_cards):
    old = {item.get("key"): item for item in (old_cards or [])}
    result = []
    for item in new_cards:
        card = dict(item)
        previous = old.get(card.get("key"))
        if previous and previous.get("status") in STATUSES:
            card["status"] = previous["status"]
        result.append(card)
    return result


def _save(org_id, cards, fingerprint, source="rules", ai_at=""):
    payload = json.dumps(cards or [])
    stamp = now_str()
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO recommendation_batches "
            "(org_id, fingerprint, source, payload, generated_at, ai_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (org_id, fingerprint, source, payload, stamp, ai_at or "", stamp, stamp))
        conn.execute(
            "UPDATE recommendation_batches SET fingerprint = ?, source = ?, payload = ?, "
            "generated_at = ?, ai_at = ?, updated_at = ? WHERE org_id = ?",
            (fingerprint, source, payload, stamp, ai_at or "", stamp, org_id))
    return _load_row(org_id)


def _ai_available():
    try:
        import llm
        return bool(llm.configured_providers())
    except Exception:
        return False


def _ai_job_open(org_id):
    if org_id is None:
        return False
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM jobs WHERE task = ? AND org_id = ? "
                "AND status IN ('queued', 'running') ORDER BY id DESC LIMIT 1",
                ("recommendations", org_id)).fetchone()
        return bool(row)
    except Exception:
        return False


def _queue_ai(org_id, requester_id=None, force=False):
    if org_id is None or not _ai_available() or _ai_job_open(org_id):
        return False
    row = _load_row(org_id)
    if row and row["ai_at"] and row["ai_at"] >= _cutoff(AI_MIN_MINUTES / 60.0):
        return False
    if row and not force and row["ai_at"] and row["ai_at"] >= _cutoff(AI_RETRY_HOURS):
        return False
    try:
        import jobs
        job_id = jobs.create_job(
            "Recommendations: client %s" % org_id,
            "Rank and reword this client's evidence-backed next steps.",
            requester_id, "selector", "recommendations", org_id=org_id,
            read_only=True,
        )
        try:
            jobs.submit(job_id)
        except Exception:
            jobs.cancel_job(job_id)
            return False
        with get_connection() as conn:
            conn.execute(
                "UPDATE recommendation_batches SET ai_at = ?, updated_at = ? "
                "WHERE org_id = ?", (now_str(), now_str(), org_id))
        return True
    except Exception:
        return False


def get(org_id, include_admin=True, requester_id=None, queue_ai=True, force=False):
    """Return the current batch, regenerating rules when the evidence changed."""
    empty = {"recommendations": [], "generated_at": "", "source": "rules",
             "refreshing": False, "stale": False}
    if org_id is None:
        return empty
    facts = signals(org_id)
    fingerprint = _fingerprint(facts)
    row = _load_row(org_id)
    stale = (not row or row["fingerprint"] != fingerprint
             or row["generated_at"] < _cutoff(CACHE_HOURS))
    if stale or force:
        old_cards = _payload(row)
        old_ai_at = row["ai_at"] if row else ""
        cards = _preserve(old_cards, candidates(facts))
        # A changed fact should ask for fresh AI wording. A manual refresh keeps the
        # recent-attempt timestamp so repeated clicks cannot spend another model call.
        keep_ai_at = old_ai_at if (force and not stale) else ""
        row = _save(org_id, cards, fingerprint, source="rules", ai_at=keep_ai_at)
        stale = False
    if queue_ai and (force or (row and row["source"] != "ai")):
        _queue_ai(org_id, requester_id=requester_id, force=force)
    result = _public(row, include_admin)
    result["stale"] = bool(stale)
    return result


def refresh(org_id, requester_id=None, include_admin=True):
    return get(org_id, include_admin=include_admin, requester_id=requester_id,
               queue_ai=True, force=True)


def set_status(org_id, key, status, include_admin=False):
    if org_id is None or status not in STATUSES:
        return None
    row = _load_row(org_id)
    if not row:
        return None
    cards = _payload(row)
    changed = False
    for card in cards:
        if card.get("key") == key:
            card["status"] = status
            changed = True
            break
    if not changed:
        return None
    _save(org_id, cards, row["fingerprint"], source=row["source"],
          ai_at=row["ai_at"] or "")
    return _public(_load_row(org_id), include_admin)


def _public(row, include_admin=True):
    cards = [item for item in _payload(row)
             if item.get("status", "open") == "open"
             and (include_admin or item.get("audience") != "admin")]
    cards.sort(key=lambda item: (item.get("priority", 9), item.get("key", "")))
    return {
        "recommendations": cards[:MAX_CARDS],
        "generated_at": row["generated_at"] if row else "",
        "source": row["source"] if row else "rules",
        "refreshing": _ai_job_open(row["org_id"]) if row else False,
        "stale": False,
    }


def _result_text(result):
    for message in reversed(list(getattr(result, "messages", []) or [])):
        content = getattr(message, "content", None)
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                text = getattr(item, "text", None)
                if text is None and isinstance(item, dict):
                    text = item.get("text")
                if text:
                    parts.append(str(text))
            if parts:
                return "\n".join(parts)
    return ""


def _parse_ai(text, candidates, old_cards=None):
    """Merge AI wording onto real candidates; any unknown key is discarded."""
    raw = str(text or "")
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        payload = json.loads(raw[start:end + 1])
    except (TypeError, ValueError):
        return []
    items = payload.get("recommendations") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []
    by_key = {item.get("key"): item for item in (candidates or [])
              if item.get("key")}
    old = {item.get("key"): item for item in (old_cards or [])}
    out = []
    for item in items[:MAX_CARDS]:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        base = by_key.get(key)
        if not base or any(card.get("key") == key for card in out):
            continue
        card = dict(base)
        title = " ".join(str(item.get("title") or "").split())[:120]
        why = " ".join(str(item.get("why") or "").split())[:320]
        action = " ".join(str(item.get("action_text") or "").split())[:500]
        allowed_numbers = set(re.findall(
            r"\d+(?:\.\d+)?%?",
            json.dumps({key: base.get(key) for key in
                        ("title", "why", "action_text", "evidence", "category")},
                       ensure_ascii=False)))
        if any(set(re.findall(r"\d+(?:\.\d+)?%?", text)) - allowed_numbers
               for text in (title, why, action)):
            # A new number is an invented metric, not a rephrasing. Keep the
            # deterministic card (or omit it) rather than showing made-up evidence.
            continue
        if title:
            card["title"] = title
        if why:
            card["why"] = why
        if action:
            card["action_text"] = action
            card["ask_prompt"] = action
        card["source"] = "ai"
        card["priority"] = len(out) + 1
        previous = old.get(key)
        card["status"] = (previous or {}).get("status", "open")
        out.append(card)
    return out


async def refine(org_id, job_id=None, emit=None):
    """Optional AI ranking/rephrasing. The deterministic batch is never lost."""
    row = _load_row(org_id)
    if not row:
        return "No recommendation batch to refine."
    fingerprint = row["fingerprint"]
    cards = [item for item in _payload(row) if item.get("status") == "open"]
    if not cards:
        return "No open recommendations to refine."
    try:
        import llm
        from autogen_agentchat.agents import AssistantAgent

        providers = llm.configured_providers()
        if not providers:
            return "No AI provider configured; deterministic ideas kept."
        provider = "deepseek" if "deepseek" in providers else providers[0]
        prompt = (
            "You are a practical growth advisor for a small business. Rank and reword "
            "the candidates below into at most five next actions. Use ONLY these "
            "candidates. Never invent a metric, event, name or action. Keep each item's "
            "key, and return JSON exactly like "
            '{"recommendations":[{"key":"...","title":"...","why":"...",'
            '"action_text":"..."}]}. Write plain English, no jargon, and make the action '
            "a sentence the owner can send to their assistant.\n\nCandidates:\n"
            + json.dumps([{key: item.get(key) for key in
                           ("key", "title", "why", "action_text", "evidence",
                            "category", "priority")} for item in cards], indent=1)
        )
        client = llm.get_model_client(provider, usage_label="recommendations")
        agent = AssistantAgent(
            name="RecommendationAdvisor",
            model_client=client,
            system_message=("You are a concise, evidence-led growth advisor. Return only "
                            "the requested JSON."),
            reflect_on_tool_use=False,
        )
        result = await agent.run(task=prompt)
        merged = _parse_ai(_result_text(result), cards, _payload(row))
        if not merged:
            return "The AI returned no usable recommendation; deterministic ideas kept."
        current = _load_row(org_id)
        if not current or current["fingerprint"] != fingerprint:
            return "Client facts changed while the AI was thinking; kept the newer batch."
        merged = _preserve(_payload(current), merged)
        _save(org_id, merged, fingerprint, source="ai", ai_at=now_str())
        if emit:
            emit("Recommendations", "system",
                 "Refreshed %d idea(s) with AI wording." % len(merged))
        return "Refreshed %d recommendation(s)." % len(merged)
    except Exception as exc:
        if emit:
            emit("Recommendations", "error",
                 "AI refinement failed; the deterministic ideas are still shown: %s"
                 % str(exc)[:200])
        return "AI refinement failed; deterministic ideas kept."


try:
    import jobs as _jobs

    @_jobs.task("recommendations")
    async def _recommendations_task(job, emit):
        """Job task: optionally refine the cached deterministic batch."""
        org_id = job.get("org_id")
        if org_id is None:
            return "No client attached."
        return await refine(org_id, job_id=job.get("id"), emit=emit)
except Exception:
    # A bare import in a test or an incomplete deployment must not make the module
    # unusable; the deterministic API still works.
    pass

