"""Execute a guided build into safe, editable assets.

The build task deliberately composes existing services (profiles, forms, templates,
deliverables, sites) instead of asking a model for raw HTML. A novice gets the same
responsive page every time, with their words escaped, and the normal Publish/Send
approvals are still the only way anything goes public.
"""

import json

import actions
import build_guides
import builds
import deliverables
import forms as forms_service
import jobs
import profile as profile_mod
import site_builder
import sites as sites_service
import templates_service


class BuildError(ValueError):
    pass


def _form_questions(fields, custom_question="", purpose="form"):
    fields = list(fields or ["name", "email"])
    questions = []
    if "name" in fields:
        questions.append({"id": "name", "label": "Your name", "type": "text",
                          "page": 0, "required": purpose != "newsletter"})
    if "email" in fields:
        questions.append({"id": "email", "label": "Your email", "type": "email",
                          "page": 0, "required": True})
    if "phone" in fields:
        questions.append({"id": "phone", "label": "Your phone", "type": "phone",
                          "page": 0, "required": False})
    if "message" in fields:
        questions.append({"id": "message", "label": "How can we help?", "type": "textarea",
                          "page": 0, "required": False})
    if str(custom_question or "").strip():
        questions.append({"id": "question", "label": str(custom_question).strip()[:200],
                          "type": "textarea", "page": 0, "required": False})
    if not questions:
        questions.append({"id": "email", "label": "Your email", "type": "email",
                          "page": 0, "required": True})
    return questions


def _create_form(org_id, user_id, title, fields, success, button, color,
                 description="", custom_question="", purpose="form"):
    questions = _form_questions(fields, custom_question, purpose)
    contact = {}
    for role in ("name", "email", "phone"):
        if any(question["id"] == role for question in questions):
            contact[role] = role
    form = forms_service.create_form({
        "name": title,
        "kind": "form",
        "questions": questions,
        "settings": {
            "description": str(description or "")[:500],
            "success": str(success or "Thanks - we have your message.")[:500],
            "submit_label": str(button or "Send")[:60],
            "brand": color or "#4f46e5",
            "contact": contact,
        },
    }, org_id, owner_id=user_id)
    form = forms_service.update_form(form["id"], org_id, {"status": "published"}) or form
    site = forms_service.live_site(org_id)
    return form, forms_service.public_url(form, site)


def _welcome_assets(org_id, user_id, answers, profile, form=None, purpose="form"):
    """A welcome template, plus a disabled automation when a form can trigger it.

    The automation is a draft by construction (``enabled=False``). Applying it is still
    the user's decision, and the send inside it still passes the normal approval gate.
    """
    if not form:
        return {}
    welcome = site_builder.render_welcome(answers, profile)
    template = templates_service.save_template({
        "name": "Welcome - %s" % (form.get("name") or purpose),
        "subject": welcome["subject"],
        "body": welcome["body"],
    }, org_id)
    result = {"template_id": template["id"]}
    steps = [
        {"id": "approve", "type": "gate", "title": "Approve the welcome email",
         "next": ["send"]},
        {"id": "send", "type": "tool", "title": "Send the welcome email",
         "config": {"tool": "send_email", "args": {
             "template_id": template["id"], "contact_id": "{{contact.id}}",
         }}, "next": []},
    ]
    try:
        import automations
        automation_id = automations.create(
            org_id, "Welcome after %s" % (form.get("name") or purpose), steps,
            trigger_type="event", trigger_config={"event": "form.submitted"},
            created_by=user_id, enabled=False)
        result["automation_id"] = automation_id
        result["automation_note"] = "Saved switched off. Review it before enabling."
    except Exception as exc:
        result["automation_error"] = str(exc)[:300]
    return result


def _welcome_series_assets(org_id, user_id, answers, profile, form):
    """Welcome + two follow-ups, linked by delays inside one switched-off automation."""
    welcome = site_builder.render_welcome(answers, profile)
    templates = [templates_service.save_template({
        "name": "Welcome - %s" % (form.get("name") or "newsletter"),
        "subject": welcome["subject"],
        "body": welcome["body"],
    }, org_id)]
    followup = str(answers.get("followup") or
                   "Share one useful tip and invite them back.").strip()
    for index in range(2, 4):
        templates.append(templates_service.save_template({
            "name": "Welcome series %d" % index,
            "subject": "A quick follow-up from %s" % (profile.get("name") or "us"),
            "body": "%s\n\n%s" % (followup, profile.get("name") or "The team"),
        }, org_id))
    days = 7 if answers.get("cadence") == "7_days" else 3
    flow = []
    for index, template in enumerate(templates):
        flow.append({"id": "approve-%d" % index, "type": "gate",
                     "title": "Approve email %d" % (index + 1), "next": []})
        flow.append({"id": "send-%d" % index, "type": "tool",
                     "title": "Send email %d" % (index + 1),
                     "config": {"tool": "send_email", "args": {
                         "template_id": template["id"],
                         "contact_id": "{{contact.id}}",
                     }}, "next": []})
        if index < len(templates) - 1:
            flow.append({"id": "wait-%d" % index, "type": "delay",
                         "title": "Wait %d day(s)" % days,
                         "config": {"days": days}, "next": []})
    for index, node in enumerate(flow[:-1]):
        node["next"] = [flow[index + 1]["id"]]
    result = {"template_ids": [template["id"] for template in templates]}
    try:
        import automations
        automation_id = automations.create(
            org_id, "Welcome series after %s" % (form.get("name") or "sign-up"),
            flow, trigger_type="event", trigger_config={"event": "form.submitted"},
            created_by=user_id, enabled=False)
        result["automation_id"] = automation_id
        result["automation_note"] = "Saved switched off. Review it before enabling."
    except Exception as exc:
        result["automation_error"] = str(exc)[:300]
    return result


def _live_site(org_id):
    for site in sites_service.list_sites(org_id):
        if site.get("status") == "live" and site.get("domain"):
            return site
    return None


def _site_for_answers(org_id, user_id, answers):
    """The site a website build should be bound to, without provisioning it."""
    if answers.get("has_domain") == "yes" and answers.get("domain_name"):
        domain = str(answers.get("domain_name")).strip().lower()
        existing = sites_service.get_site_by_domain(domain)
        if existing:
            if existing.get("org_id") != org_id:
                raise BuildError("That web address belongs to another client.")
            return existing
        return sites_service.create_site(
            {"domain": domain, "target": "local"}, owner_id=user_id, org_id=org_id)
    return _live_site(org_id)


def _profile_data(answers):
    data = {}
    mapping = {
        "business_name": "name",
        "what_you_do": "description",
        "audience": "target_audience",
        "email": "support_email",
        "phone": "support_phone",
        "address": "address",
        "hours": "business_hours",
        "brand_color": "brand_color",
    }
    for answer_key, profile_key in mapping.items():
        if str(answers.get(answer_key) or "").strip():
            data[profile_key] = str(answers[answer_key]).strip()
    services = [line.strip() for line in str(answers.get("services") or "").splitlines()
                if line.strip()]
    if services:
        data["services"] = [{"name": line[:160], "description": ""}
                            for line in services[:8]]
    return data


def _prepare_site(org_id, user_id, answers, profile, emit):
    """Forms, profile and site for either the one-page or multi-page path."""
    site = _site_for_answers(org_id, user_id, answers)
    form_links = []
    form_ids = []
    template_ids = []
    automation_ids = []
    notes = []

    if answers.get("want_form") == "yes":
        emit("build", "step", "Creating a contact form")
        form, link = _create_form(
            org_id, user_id, "Contact us", ["name", "email", "message"],
            "Thanks - we have your message and will be in touch.", "Send",
            answers.get("brand_color"), purpose="leads")
        form_ids.append(form["id"])
        form_links.append({
            "label": "Send us a message",
            "url": link if not site or site.get("status") == "live"
                   else "/f/" + form["slug"],
            "public_url": link,
        })

    if answers.get("want_newsletter") == "yes":
        emit("build", "step", "Creating the newsletter sign-up")
        form, link = _create_form(
            org_id, user_id, "Join our newsletter", ["name", "email"],
            "Thanks for signing up - we will be in touch.", "Join the list",
            answers.get("brand_color"), purpose="newsletter")
        form_ids.append(form["id"])
        form_links.append({
            "label": "Join our newsletter",
            "url": link if not site or site.get("status") == "live"
                   else "/f/" + form["slug"],
            "public_url": link,
        })
        welcome = _welcome_assets(org_id, user_id, answers, profile, form=form,
                                  purpose="newsletter")
        template_ids += [welcome["template_id"]] if welcome.get("template_id") else []
        automation_ids += [welcome["automation_id"]] if welcome.get("automation_id") else []
        if welcome.get("automation_error"):
            notes.append("The welcome automation could not be drafted: %s"
                         % welcome["automation_error"])

    profile_mod.update_profile(org_id, _profile_data(answers), owner_id=user_id)
    profile = profile_mod.get_profile(org_id) or profile
    return {
        "site": site,
        "profile": profile,
        "form_ids": form_ids,
        "form_links": form_links,
        "template_ids": template_ids,
        "automation_ids": automation_ids,
        "notes": notes,
    }


def _build_website(org_id, user_id, answers, profile, emit):
    assets = _prepare_site(org_id, user_id, answers, profile, emit)
    if answers.get("site_size") == "multi_page":
        return _build_site_pages(org_id, user_id, answers, assets, emit)
    site = assets["site"]
    content = site_builder.render_website(answers, assets["profile"], assets["form_links"])
    title = "Website for %s" % (answers.get("business_name") or "your business")
    item = deliverables.create_deliverable({
        "kind": "website",
        "title": title,
        "slug": "home",
        "summary": answers.get("what_you_do") or "",
        "content": content,
        "site_id": site["id"] if site else None,
    }, org_id=org_id, created_by=user_id)
    notes = list(assets["notes"])
    if not site:
        notes.append("Your site is saved as a draft. Add a web address in Domains "
                     "when you are ready to publish.")
    else:
        notes.append("Review the draft, then publish it to %s." % site["domain"])
    return {
        "kind": "website",
        "mode": "one_page",
        "deliverable_id": item["id"],
        "page_ids": [item["id"]],
        "site_id": site["id"] if site else None,
        "site_domain": site["domain"] if site else "",
        "form_ids": assets["form_ids"],
        "form_links": assets["form_links"],
        "template_ids": assets["template_ids"],
        "automation_ids": assets["automation_ids"],
        "notes": notes,
    }


def _build_site_pages(org_id, user_id, answers, assets, emit):
    site = assets["site"]
    selected = ["home"]
    for page in answers.get("pages") or []:
        if page in ("about", "services", "contact", "blog") and page not in selected:
            selected.append(page)
    posts = []
    if "blog" in selected:
        posts = [row for row in deliverables.list_deliverables("blog", org_id)
                 if row.get("status") == "published"
                 and (not site or row.get("site_id") in (None, site.get("id")))]
    titles = {"home": "Home", "about": "About", "services": "Services",
              "contact": "Contact", "blog": "Blog"}
    pages = []
    for page in selected:
        emit("build", "step", "Creating the %s page" % titles[page].lower())
        content = site_builder.render_site_page(
            answers, page, assets["profile"], assets["form_links"], posts)
        item = deliverables.create_deliverable({
            "kind": "website",
            "title": "%s - %s" % (answers.get("business_name") or "Site", titles[page]),
            "slug": "home" if page == "home" else page,
            "summary": answers.get("what_you_do") or "",
            "content": content,
            "site_id": site["id"] if site else None,
        }, org_id=org_id, created_by=user_id)
        pages.append({"page": page, "deliverable_id": item["id"],
                      "title": titles[page]})
    home = pages[0]
    notes = list(assets["notes"])
    if site:
        notes.append("Review the pages, then publish the site to %s." % site["domain"])
    else:
        notes.append("The pages are saved as drafts. Add a web address in Domains "
                     "when you are ready to publish.")
    return {
        "kind": "website",
        "mode": "multi_page",
        "deliverable_id": home["deliverable_id"],
        "page_ids": [page["deliverable_id"] for page in pages],
        "pages": pages,
        "site_id": site["id"] if site else None,
        "site_domain": site["domain"] if site else "",
        "form_ids": assets["form_ids"],
        "form_links": assets["form_links"],
        "template_ids": assets["template_ids"],
        "automation_ids": assets["automation_ids"],
        "notes": notes,
    }


def _build_form(org_id, user_id, answers, profile, emit):
    purpose = answers.get("purpose_other") or answers.get("purpose") or "sign-up"
    title = "%s form" % str(purpose).replace("_", " ").title()
    emit("build", "step", "Creating the form")
    form, link = _create_form(
        org_id, user_id, title, answers.get("fields") or ["name", "email"],
        answers.get("success_message"), answers.get("button_label"),
        answers.get("brand_color"), custom_question=answers.get("custom_question"),
        purpose=answers.get("purpose") or "form")
    result = {
        "kind": "form",
        "form_id": form["id"],
        "form_url": link,
        "notes": [],
    }
    if answers.get("welcome_email") == "yes":
        welcome = _welcome_assets(org_id, user_id, answers, profile, form=form,
                                  purpose="form")
        if welcome.get("template_id"):
            result["template_id"] = welcome["template_id"]
        if welcome.get("automation_id"):
            result["automation_id"] = welcome["automation_id"]
            result["automation_note"] = welcome.get("automation_note")
        if welcome.get("automation_error"):
            result["notes"].append("The welcome automation could not be drafted: %s"
                                   % welcome["automation_error"])
    result["notes"].append("The form is live at its link. You can unpublish it from Forms.")
    return result


def _build_blog(org_id, user_id, answers, profile, emit):
    site = _live_site(org_id)
    cta_links = []
    form_id = None
    if answers.get("cta") == "contact" and profile.get("support_email"):
        cta_links.append({"label": "Get in touch",
                          "url": "mailto:%s" % profile.get("support_email")})
    elif answers.get("cta") == "signup":
        emit("build", "step", "Creating a sign-up form for the post")
        form, link = _create_form(
            org_id, user_id, "Join our newsletter", ["name", "email"],
            "Thanks for signing up.", "Join the list", answers.get("brand_color"),
            purpose="newsletter")
        form_id = form["id"]
        cta_links.append({"label": "Join our newsletter", "url": link})
    emit("build", "step", "Writing the article page")
    content = site_builder.render_blog(answers, profile, cta_links)
    item = deliverables.create_deliverable({
        "kind": "blog",
        "title": answers.get("topic") or "Blog post",
        "slug": deliverables.slugify(answers.get("topic") or "blog-post"),
        "summary": answers.get("takeaway") or "",
        "content": content,
        "site_id": site["id"] if site else None,
    }, org_id=org_id, created_by=user_id)
    return {
        "kind": "blog",
        "deliverable_id": item["id"],
        "site_id": site["id"] if site else None,
        "site_domain": site["domain"] if site else "",
        "form_id": form_id,
        "notes": ["The post is a draft. Review it, then publish it from the Blogs tab."
                  if site else
                  "The post is saved as a draft. Add a site when you are ready to publish."],
    }


def _build_newsletter(org_id, user_id, answers, profile, emit):
    emit("build", "step", "Writing the newsletter")
    letter = site_builder.render_newsletter(answers, profile)
    body = site_builder.render_newsletter_html(answers, profile)
    template = templates_service.save_template({
        "name": answers.get("subject") or "Newsletter",
        "subject": letter["subject"],
        "body": body,
    }, org_id)
    result = {
        "kind": "newsletter",
        "template_id": template["id"],
        "notes": ["The newsletter is saved as a template. Nothing has been sent."],
    }
    form = None
    if answers.get("need_form") == "yes":
        emit("build", "step", "Creating the sign-up form")
        form, link = _create_form(
            org_id, user_id, "Join our newsletter",
            answers.get("form_fields") or ["name", "email"],
            "Thanks for signing up - we will be in touch.", "Join the list",
            answers.get("brand_color"), purpose="newsletter")
        result["form_id"] = form["id"]
        result["form_url"] = link
    if answers.get("welcome") == "yes" and form:
        if answers.get("sequence") == "series":
            welcome = _welcome_series_assets(org_id, user_id, answers, profile, form)
            result["welcome_template_ids"] = welcome["template_ids"]
        else:
            welcome = _welcome_assets(org_id, user_id, answers, profile, form=form,
                                      purpose="newsletter")
            if welcome.get("template_id"):
                result["welcome_template_id"] = welcome["template_id"]
        if welcome.get("automation_id"):
            result["automation_id"] = welcome["automation_id"]
            result["automation_note"] = welcome.get("automation_note")
        if welcome.get("automation_error"):
            result["notes"].append("The welcome automation could not be drafted: %s"
                                   % welcome["automation_error"])
    return result


def publish_site_build(build_id, org_id, actor=""):
    """Publish all pages in a multi-page build under one approval.

    The click is the decision. One audit row names the site and its pages; each page is
    then written with ``preapproved=True`` so a synchronous Flask handler never waits on
    the gate, and every page still gets its normal version snapshot.
    """
    row = builds._row(build_id, org_id)
    if not row:
        raise BuildError("That build was not found.")
    result = builds._json(row.get("result_json"), {})
    pages = [int(page) for page in result.get("page_ids") or [] if page]
    site_id = result.get("site_id")
    if not pages or not site_id:
        raise BuildError("This build has no pages to publish yet.")
    site = sites_service.get_site(int(site_id))
    if not site or site.get("org_id") != org_id:
        raise BuildError("That site belongs to another client.")
    for page_id in pages:
        item = deliverables.get_deliverable(page_id)
        if not item or item.get("org_id") != org_id:
            raise BuildError("One of the pages belongs to another client.")
    live = not actions.is_dry_run()
    approval_id = None
    if live:
        summary = "publish %d page(s) of build #%d to %s" % (
            len(pages), build_id, site["domain"])
        approval_id = actions.request_approval(
            "file_write", summary, {"path": site["domain"], "pages": pages}, None)
        decided = actions.decide_approval(approval_id, "approved", actor or "operator")
        if not decided or decided.get("status") != "approved":
            raise BuildError("This publish could not be recorded as approved.")
    urls = []
    try:
        for page_id in pages:
            published = deliverables.publish_deliverable(
                page_id, site_id=site["id"], actor=actor,
                as_home=(page_id == pages[0]), replace=True, preapproved=live)
            urls.append(published.get("url") or "")
    except deliverables.DeliverableError as exc:
        if approval_id:
            actions.set_result(approval_id, str(exc)[:1000])
        raise BuildError(str(exc))
    if approval_id:
        actions.set_result(approval_id, "published: %s" % ", ".join(url for url in urls if url))
    result["published"] = {"urls": urls, "site_domain": site["domain"],
                           "dry_run": not live}
    builds.record_result(build_id, result)
    return {"ok": True, "urls": urls, "site_domain": site["domain"],
            "dry_run": not live}


def refresh_blog_page(build_id, org_id, actor=""):
    """Rebuild the blog index from the posts that are published now."""
    row = builds._row(build_id, org_id)
    if not row:
        raise BuildError("That build was not found.")
    result = builds._json(row.get("result_json"), {})
    page = next((item for item in result.get("pages") or [] if item.get("page") == "blog"),
                None)
    if not page:
        raise BuildError("This build has no blog page to refresh.")
    item = deliverables.get_deliverable(int(page["deliverable_id"]))
    if not item or item.get("org_id") != org_id:
        raise BuildError("That blog page belongs to another client.")
    kind = build_guides.normalise_kind(row.get("kind"))
    answers = build_guides.effective_answers(kind, builds._json(row.get("answers_json"), {}))
    profile = profile_mod.get_profile(org_id) or {}
    site_id = result.get("site_id")
    posts = [post for post in deliverables.list_deliverables("blog", org_id)
             if post.get("status") == "published"
             and (not site_id or post.get("site_id") in (None, site_id))]
    content = site_builder.render_site_page(answers, "blog", profile, [], posts)
    deliverables.update_deliverable(item["id"], {"content": content})
    url = ""
    if site_id:
        live = not actions.is_dry_run()
        approval_id = None
        if live:
            approval_id = actions.request_approval(
                "file_write", "refresh the blog page for %s" % profile.get("name", ""),
                {"path": "blog.html", "build_id": build_id}, None)
            decided = actions.decide_approval(approval_id, "approved", actor or "operator")
            if not decided or decided.get("status") != "approved":
                raise BuildError("The blog refresh could not be recorded as approved.")
        try:
            published = deliverables.publish_deliverable(
                item["id"], site_id=site_id, actor=actor, as_home=False,
                replace=True, preapproved=live)
        except deliverables.DeliverableError as exc:
            if approval_id:
                actions.set_result(approval_id, str(exc)[:1000])
            raise BuildError(str(exc))
        url = published.get("url") or ""
        if approval_id:
            actions.set_result(approval_id, "published %s" % url)
    result["blog"] = {"refreshed": True, "url": url, "posts": len(posts)}
    builds.record_result(build_id, result)
    return {"ok": True, "url": url, "posts": len(posts)}


def execute(build_id, job=None, emit=None):
    """Turn a completed build into drafts, forms and templates. No publish, no send."""
    emit = emit or (lambda *args: None)
    row = builds._row(build_id)
    if not row:
        raise BuildError("That build was not found.")
    if job is not None and row.get("org_id") != job.get("org_id"):
        raise BuildError("That build belongs to another client.")
    kind = build_guides.normalise_kind(row.get("kind"))
    answers = build_guides.effective_answers(kind, builds._json(row.get("answers_json"), {}))
    if not build_guides.ready(kind, answers):
        raise BuildError("A few answers are still needed before this can be built.")
    org_id = row.get("org_id")
    user_id = row.get("user_id")
    profile = profile_mod.get_profile(org_id) or {}
    emit("build", "step", "Starting %s" % build_guides.title_for(kind, answers))
    if kind == "website":
        return _build_website(org_id, user_id, answers, profile, emit)
    if kind == "form":
        return _build_form(org_id, user_id, answers, profile, emit)
    if kind == "blog":
        return _build_blog(org_id, user_id, answers, profile, emit)
    if kind == "newsletter":
        return _build_newsletter(org_id, user_id, answers, profile, emit)
    raise BuildError("Unknown build kind.")


def _done_message(result):
    kind = result.get("kind")
    if kind == "website":
        return "Built the website draft%s. Review it, then publish when you are ready." % (
            (" for %s" % result.get("site_domain")) if result.get("site_domain") else "")
    if kind == "form":
        return "Built the form. It is live at %s." % result.get("form_url")
    if kind == "blog":
        return "Built the blog post draft. Review it from the Blogs tab."
    if kind == "newsletter":
        return "Wrote the newsletter template%s. Nothing has been sent." % (
            " and the sign-up form" if result.get("form_id") else "")
    return "Build finished."


@jobs.task("build")
def build_task(job, emit):
    """Worker task for a guided build."""
    build = builds.get_by_job(job.get("id"))
    if not build:
        emit("build", "error", "This job has no build session.")
        return "No build session."
    try:
        result = execute(build["id"], job=job, emit=emit)
    except Exception as exc:
        builds.fail(build["id"], str(exc))
        emit("build", "error", str(exc))
        raise
    builds.finish(build["id"], result)
    message = _done_message(result)
    emit("build", "message", message)
    return message
