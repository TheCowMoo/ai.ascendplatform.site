"""Form tools for the Manager: read a form, change it, and read its answers.

``create_form``/``list_forms``/``add_form_link`` live in ``tools.manager`` because
they predate this module. This module fills the gap that made "rewrite the six
questions" impossible: there was no way to read the definition, and no update tool
at all.

Ownership comes from the job row. A form with answers keeps its existing question
ids: renaming or deleting an id would leave the stored answers pointing at questions
that no longer exist. Labels, options, help text, logic and settings can change;
adding a question is allowed, because old answers are still readable.
"""

import json
import re

import forms as forms_service
import forms_engine
from tools import access, register

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _org(job_id):
    return access.context(job_id)["org_id"]


def _link(form, org_id):
    return forms_service.link_for_org(form, org_id)


def _questions(value):
    if value in (None, ""):
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise forms_service.FormServiceError("Questions must be a JSON array.")
    if not isinstance(value, list):
        raise forms_service.FormServiceError("Questions must be a JSON array.")
    return value


def _settings(value):
    if value in (None, ""):
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise forms_service.FormServiceError("Settings must be a JSON object.")
    if not isinstance(value, dict):
        raise forms_service.FormServiceError("Settings must be a JSON object.")
    return value


def _merged_settings(current, patch, question_ids):
    merged = dict(current or {})
    if patch is None:
        return merged
    merged.update(patch)
    for key in ("description", "success"):
        if key in merged:
            merged[key] = str(merged.get(key) or "")[:500]
    if "submit_label" in merged:
        merged["submit_label"] = str(merged.get("submit_label") or "")[:60]
    if "brand" in merged:
        brand = str(merged.get("brand") or "").strip()
        if brand and not _HEX.match(brand):
            raise forms_service.FormServiceError(
                "Brand colour must look like #4f46e5.")
        merged["brand"] = brand
    if "create_contact" in merged:
        merged["create_contact"] = bool(merged.get("create_contact"))
    contact = merged.get("contact")
    if isinstance(contact, dict):
        merged["contact"] = {
            role: qid for role, qid in contact.items()
            if role in ("name", "email", "phone") and qid in question_ids
        }
    return merged


def _form_payload(form, org_id):
    return {
        "id": form["id"],
        "name": form["name"],
        "kind": form["kind"],
        "status": form["status"],
        "slug": form["slug"],
        "public_url": _link(form, org_id),
        "questions": form.get("questions") or [],
        "settings": form.get("settings") or {},
        "pages": form.get("pages") or [],
        "question_count": form.get("question_count", 0),
    }


def _build_read_form(emit, job_id=None):
    async def read_form(form_id: int) -> str:
        """Read one form's complete definition, including questions and settings."""
        org_id = _org(job_id)
        if org_id is None:
            return "Pick a client first - forms belong to a client."
        form = forms_service.get_form(int(form_id or 0), org_id)
        if not form:
            return "There is no form %s for this client." % form_id
        return json.dumps(_form_payload(form, org_id), indent=1)

    return read_form


def _build_update_form(emit, job_id=None):
    async def update_form(form_id: int, name: str = "", kind: str = "",
                          questions: str = "", settings: str = "",
                          status: str = "") -> str:
        """Change a form after reading it. Keep existing question ids when it has answers.

        ``questions`` is a JSON array of complete question objects. ``settings`` is a
        JSON object of the form-page settings (description, success, submit_label,
        brand, contact, outcomes); it is merged over the current settings, not used to
        replace them.
        """
        ctx, error = access.manage_context(job_id, "change forms")
        if error:
            return error
        org_id = ctx["org_id"]
        name = str(name or "").strip()
        kind = str(kind or "").strip()
        status = str(status or "").strip()
        current = forms_service.get_form(int(form_id or 0), org_id)
        if not current:
            return "There is no form %s for this client." % form_id
        try:
            rows = _questions(questions)
            patch = _settings(settings)
        except forms_service.FormServiceError as exc:
            return "Not saved: %s" % exc
        if rows is not None:
            bad = [q for q in rows
                   if not isinstance(q, dict) or not str(q.get("id") or "").strip()]
            if bad:
                return ("Not saved: every question needs an id. Read the form first and "
                        "return the complete question objects, not partial rows.")
            current_ids = [q["id"] for q in forms_engine.question_list(current)]
            new_ids = [str(q.get("id") or "").strip() for q in rows if isinstance(q, dict)]
            answers = forms_service.list_responses(current["id"], org_id, limit=1)
            if answers:
                missing = [qid for qid in current_ids if qid not in new_ids]
                if missing:
                    return ("Not saved: this form already has answers, so keep these question "
                            "ids unchanged: %s. Change labels, help text, options or order "
                            "instead; add a new question with a new id if you need one."
                            % ", ".join(missing))
        else:
            rows = current.get("questions") or []
        question_ids = {str(q.get("id") or "").strip() for q in rows if isinstance(q, dict)}
        try:
            merged = _merged_settings(current.get("settings") or {}, patch, question_ids)
        except forms_service.FormServiceError as exc:
            return "Not saved: %s" % exc
        if not (name or kind or status or questions or settings):
            return "Nothing to change: give a name, kind, questions, settings or status."
        data = {"questions": rows, "settings": merged}
        if name:
            data["name"] = name
        if kind:
            data["kind"] = kind.lower()
        if status:
            data["status"] = status.lower()
        try:
            form = forms_service.update_form(current["id"], org_id, data)
        except forms_service.FormServiceError as exc:
            return "Not saved: %s" % exc
        if not form:
            return "There is no form %s for this client." % form_id
        message = {
            "ok": True,
            "id": form["id"],
            "status": form["status"],
            "public_url": _link(form, org_id),
            "warning": ("This form is published; the change is visible at its link now."
                        if form.get("status") == "published" else ""),
        }
        return json.dumps(message, indent=1)

    return update_form


def _build_list_form_responses(emit, job_id=None):
    async def list_form_responses(form_id: int, limit: int = 20) -> str:
        """Read recent answers to one form. Read-only, and client-admin only."""
        ctx, error = access.manage_context(job_id, "read form answers")
        if error:
            return error
        org_id = ctx["org_id"]
        form = forms_service.get_form(int(form_id or 0), org_id)
        if not form:
            return "There is no form %s for this client." % form_id
        try:
            wanted = max(1, min(50, int(limit or 20)))
        except (TypeError, ValueError):
            wanted = 20
        rows = forms_service.list_responses(form["id"], org_id, limit=wanted)
        return json.dumps({
            "form": {"id": form["id"], "name": form["name"],
                     "public_url": _link(form, org_id)},
            "responses": [{
                "submitted_at": row.get("created_at"),
                "score": row.get("score"),
                "max_score": row.get("max_score"),
                "outcome": row.get("outcome"),
                "contact_id": row.get("contact_id"),
                "answers": row.get("answers") or {},
            } for row in rows],
        }, indent=1)

    return list_form_responses


register("read_form",
         "Read a form's complete definition (questions, options, logic, settings and "
         "public link). Read-only; use it before changing a form.",
         _build_read_form)

register("update_form",
         "Change a form's questions, name, kind, status or page settings. Read the form "
         "first. When answers exist, keep every existing question id; change labels, "
         "help, options and order instead. Published forms change immediately.",
         _build_update_form)

register("list_form_responses",
         "Read recent answers to one form, with score, outcome and contact id. "
         "Read-only and client-admin only.",
         _build_list_form_responses)
