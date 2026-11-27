"""Agent tools for chat assistants: create one, edit it, and put it on a site.

The Chatbots screen already exists, but the Manager had no way to reach it. The
site row also stored a ``linked_bot_id`` that nothing rendered. These tools close
both gaps without exposing webhooks, channel tokens or provider keys.

Attaching is a site change plus a normal provisioning job. The job's approval card
is the boundary; this tool never writes a file itself.
"""

import json

import actions
import bots as bots_service
import forms as forms_service
import settings
import sites as sites_service
import widget_embed
from tools import access, register


def _org(job_id):
    return access.context(job_id)["org_id"]


def _as_bool(value, default=True):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("0", "false", "no", "off", "")


def _sites_for_bot(bot_id, org_id):
    if org_id is None:
        return []
    return [site.get("domain") for site in sites_service.list_sites(
        org_id, include_content=False) if site.get("linked_bot_id") == bot_id]


def _summary(bot, org_id):
    return {
        "id": bot["id"],
        "name": bot["name"],
        "assistant_name": bot.get("assistant_name") or "",
        "welcome_message": bot.get("welcome_message") or "",
        "quick_replies": bot.get("quick_replies") or [],
        "messages": bot.get("message_count", 0),
        "sites": _sites_for_bot(bot["id"], org_id),
    }


def _fallback_form(org_id, form_id=0):
    if form_id:
        return forms_service.get_form(int(form_id), org_id)
    for form in forms_service.list_forms(org_id):
        if form.get("status") == "published":
            return form
    return None


def _build_list_chat_assistants(emit, job_id=None):
    async def list_chat_assistants() -> str:
        """List this client's chat assistants, their message counts and attached sites."""
        org_id = _org(job_id)
        if org_id is None:
            return "Pick a client first - assistants belong to a client."
        rows = bots_service.list_bots(org_id)
        if not rows:
            return ("No chat assistants yet. Create one with create_chat_assistant, "
                    "then attach it to a site.")
        return json.dumps([_summary(bot, org_id) for bot in rows], indent=1)

    return list_chat_assistants


def _build_read_chat_assistant(emit, job_id=None):
    async def read_chat_assistant(bot_id: str) -> str:
        """Read one assistant's full copy, knowledge base and brand settings."""
        org_id = _org(job_id)
        if org_id is None:
            return "Pick a client first - assistants belong to a client."
        bot = bots_service.get_bot(bot_id, org_id)
        if not bot:
            return "There is no chat assistant %s for this client." % bot_id
        return json.dumps(_summary(bot, org_id) | {
            "brand_color": bot.get("brand_color") or "",
            "logo_url": bot.get("logo_url") or "",
            "system_prompt": bot.get("system_prompt") or "",
            "knowledge_base": bot.get("knowledge_base") or "",
            "use_profile_kb": bool(bot.get("use_profile_kb", True)),
        }, indent=1)

    return read_chat_assistant


def _build_create_chat_assistant(emit, job_id=None):
    async def create_chat_assistant(name: str, assistant_name: str = "",
                                    welcome_message: str = "",
                                    quick_replies: str = "",
                                    knowledge_base: str = "",
                                    system_prompt: str = "",
                                    brand_color: str = "",
                                    use_profile_kb: bool = True,
                                    enquiry_form_id: int = 0) -> str:
        """Create a chat assistant for this client and return its embed snippet.

        ``quick_replies`` may be a JSON array or a comma-separated string. The
        assistant inherits the client's Business Profile knowledge base by default.
        If an enquiry form exists, the assistant is told to point visitors to it when
        it does not know the answer. Nothing is put on a site until
        attach_chat_assistant is called.
        """
        ctx, error = access.manage_context(job_id, "create chat assistants")
        if error:
            return error
        org_id = ctx["org_id"]
        try:
            form = _fallback_form(org_id, enquiry_form_id)
        except (TypeError, ValueError):
            return "That enquiry form id is not valid."
        if enquiry_form_id and not form:
            return "There is no form %s for this client." % enquiry_form_id
        if form and form.get("status") != "published":
            return "That enquiry form is still a draft; publish it before linking it."
        fallback_url = forms_service.link_for_org(form, org_id) if form else ""
        prompt = str(system_prompt or "").strip()
        if fallback_url:
            if not prompt:
                prompt = ("You are %s, a helpful support assistant for %s. Answer only "
                          "from the knowledge base. If the answer is not there, say you "
                          "do not know and do not invent details."
                          % (str(assistant_name or "").strip() or "the assistant",
                             str(name or "").strip() or "this business"))
            prompt += ("\n\nIf you cannot answer from the knowledge base, tell the visitor "
                       "to use the enquiry form: %s" % fallback_url)
        try:
            created = bots_service.create_bot({
                "name": name,
                "assistant_name": assistant_name,
                "welcome_message": welcome_message,
                "quick_replies": quick_replies,
                "knowledge_base": knowledge_base,
                "system_prompt": prompt,
                "brand_color": brand_color,
                "use_profile_kb": _as_bool(use_profile_kb, True),
            }, org_id, owner_id=ctx["requester_id"])
        except bots_service.BotError as exc:
            return "Not created: %s" % exc
        if not created:
            return "Not created: the assistant could not be saved."
        tag = widget_embed.snippet(created["id"])
        lines = ["Created chat assistant “%s” (#%s)." % (created["name"], created["id"])]
        if tag:
            lines.append("Embed snippet: %s" % tag)
        else:
            lines.append("Set Platform URL in Settings before embedding the widget.")
        lines.append("It is not on a site yet. Call attach_chat_assistant with a site "
                     "id to put it there.")
        if not settings.get("DEEPSEEK_API_KEY"):
            lines.append("Replies need the DeepSeek key; until it is configured the "
                         "widget will say the assistant is unavailable.")
        if fallback_url:
            lines.append("It will point unanswered questions to %s." % fallback_url)
        return "\n".join(lines)

    return create_chat_assistant


def _build_update_chat_assistant(emit, job_id=None):
    async def update_chat_assistant(bot_id: str, name: str = "",
                                    assistant_name: str = "",
                                    welcome_message: str = "",
                                    quick_replies: str = "",
                                    knowledge_base: str = "",
                                    system_prompt: str = "",
                                    brand_color: str = "",
                                    use_profile_kb: str = "") -> str:
        """Change an existing assistant's copy, knowledge or brand settings."""
        ctx, error = access.manage_context(job_id, "change chat assistants")
        if error:
            return error
        data = {}
        for key, value in (("name", name), ("assistant_name", assistant_name),
                           ("welcome_message", welcome_message),
                           ("quick_replies", quick_replies),
                           ("knowledge_base", knowledge_base),
                           ("system_prompt", system_prompt),
                           ("brand_color", brand_color)):
            if str(value or "").strip():
                data[key] = value
        if str(use_profile_kb or "").strip():
            data["use_profile_kb"] = _as_bool(use_profile_kb, True)
        if not data:
            return "Nothing to change: give a name, copy, knowledge or brand colour."
        try:
            updated = bots_service.update_bot(bot_id, ctx["org_id"], data)
        except bots_service.BotError as exc:
            return "Not saved: %s" % exc
        if not updated:
            return "There is no chat assistant %s for this client." % bot_id
        return ("Updated chat assistant “%s” (#%s). Its public page picks up the change "
                "on the next visitor message." % (updated["name"], updated["id"]))

    return update_chat_assistant


def _build_attach_chat_assistant(emit, job_id=None):
    async def attach_chat_assistant(site_id: int, bot_id: str) -> str:
        """Put a client's assistant on a client's site.

        The site row is updated, then a normal provisioning job is queued. The job's
        approval card is the live-write boundary; this tool never writes a page
        itself. In dry-run the desired state is recorded but nothing is queued.
        """
        ctx, error = access.manage_context(job_id, "attach a chat assistant")
        if error:
            return error
        org_id = ctx["org_id"]
        try:
            wanted_site = int(site_id or 0)
        except (TypeError, ValueError):
            return "site_id must be a number; use list_sites to find it."
        site = sites_service.get_site(wanted_site)
        if not site or site.get("org_id") != org_id:
            return "There is no site %s for this client." % site_id
        bot = bots_service.get_bot(bot_id, org_id)
        if not bot:
            return "There is no chat assistant %s for this client." % bot_id
        if not widget_embed.platform_origin():
            return ("Set Platform URL in Settings first; without it the site would get a "
                    "dead widget script.")
        try:
            updated = sites_service.update_site(site["id"], {"linked_bot_id": bot["id"]})
        except sites_service.SiteError as exc:
            return "Not attached: %s" % exc
        if not updated:
            return "There is no site %s for this client." % site_id
        if actions.is_dry_run():
            return ("Linked “%s” to %s in the site record. Dry-run is on, so no live "
                    "provisioning job was queued." % (bot["name"], updated["domain"]))
        job = sites_service.queue_provision(updated, ctx["requester_id"], manual=True)
        if not job:
            return ("Linked “%s” to %s. A provisioning run is already queued; the chat "
                    "button will appear when it finishes." % (bot["name"], updated["domain"]))
        return ("Linked “%s” to %s and queued provisioning job #%s. Approve the site "
                "update to make the chat button live." % (bot["name"], updated["domain"],
                                                          job.get("id")))

    return attach_chat_assistant


register("list_chat_assistants",
         "List this client's chat assistants with message counts and attached sites. "
         "Read-only.",
         _build_list_chat_assistants)

register("read_chat_assistant",
         "Read one chat assistant's welcome message, quick replies, system prompt, "
         "knowledge base and brand settings. Read-only.",
         _build_read_chat_assistant)

register("create_chat_assistant",
         "Create a chat assistant for this client. Returns the widget snippet; call "
         "attach_chat_assistant to put it on a site. Never accepts channel tokens, "
         "webhooks or API keys.",
         _build_create_chat_assistant)

register("update_chat_assistant",
         "Change an existing chat assistant's copy, knowledge base or brand colour.",
         _build_update_chat_assistant)

register("attach_chat_assistant",
         "Link a chat assistant to one of this client's sites and queue the normal "
         "site provisioning approval. Use list_chat_assistants/list_sites first.",
         _build_attach_chat_assistant)
