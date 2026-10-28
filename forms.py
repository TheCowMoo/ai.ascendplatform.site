"""Forms, surveys and quizzes: the document, its responses, and what a submission means.

The definition is stored as one JSON document (pages/questions/settings) and validated by
:mod:`forms_engine` on the way in, so an invalid graph cannot be saved and then fail under a
visitor's feet. Submissions are validated against the same engine, which is what makes the
public endpoint safe to expose: a browser that lies about which questions it showed still
gets its answers checked against the rules the server believes in.
"""

import json
import re

import forms_engine
from db import get_connection, now_str

JSON_FIELDS = ("pages", "questions", "settings")


class FormServiceError(ValueError):
    pass


def _slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return (slug or "form")[:60]


def _unique_slug(conn, org_id, base):
    """A slug is globally unique: it is a public URL, and /f/italy-quiz must mean one thing."""
    slug, n = base, 1
    while conn.execute("SELECT id FROM forms WHERE slug = ?", (slug,)).fetchone():
        n += 1
        slug = "%s-%d" % (base, n)
    return slug


def form_out(row, responses=0):
    if not row:
        return None
    out = dict(row)
    for field in JSON_FIELDS:
        raw = out.get(field)
        if isinstance(raw, str):
            try:
                out[field] = json.loads(raw)
            except ValueError:
                out[field] = {} if field == "settings" else []
    out["questions"] = forms_engine.question_list(out)
    out["question_count"] = len(out["questions"])
    out["page_count"] = max([forms_engine.page_of(q) for q in out["questions"]] + [0]) + 1
    out["responses"] = responses
    return out


def list_forms(org_id, limit=100):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM forms WHERE org_id = ? ORDER BY id DESC LIMIT %d"
            % max(1, int(limit)), (org_id,)).fetchall()
        counts = {}
        for row in conn.execute(
                "SELECT form_id, COUNT(*) AS n FROM form_responses WHERE org_id = ? "
                "GROUP BY form_id", (org_id,)).fetchall():
            counts[row["form_id"]] = row["n"]
    return [form_out(row, counts.get(row["id"], 0)) for row in rows]


def get_form(form_id, org_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM forms WHERE id = ? AND org_id = ?",
                           (form_id, org_id)).fetchone()
    return form_out(row)


def get_by_slug(slug, published_only=True):
    query = "SELECT * FROM forms WHERE slug = ?"
    if published_only:
        query += " AND status = 'published'"
    with get_connection() as conn:
        row = conn.execute(query, (slug,)).fetchone()
    return form_out(row)


def create_form(data, org_id, owner_id=None):
    data = data or {}
    name = str(data.get("name") or "").strip()[:200] or "Untitled form"
    kind = str(data.get("kind") or "form").strip().lower()
    questions = data.get("questions") or []
    settings = data.get("settings") or {}
    try:
        forms_engine.validate({"kind": kind, "questions": questions, "settings": settings})
    except forms_engine.FormError as exc:
        raise FormServiceError(str(exc))
    with get_connection() as conn:
        slug = _unique_slug(conn, org_id, _slugify(data.get("slug") or name))
        cur = conn.execute(
            "INSERT INTO forms (org_id, name, slug, kind, pages, questions, settings, status, "
            "owner_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (org_id, name, slug, kind, json.dumps(data.get("pages") or []),
             json.dumps(questions), json.dumps(settings), "draft", owner_id,
             now_str(), now_str()))
        new_id = cur.lastrowid
    return get_form(new_id, org_id)


def update_form(form_id, org_id, data):
    current = get_form(form_id, org_id)
    if not current:
        return None
    data = data or {}
    kind = str(data.get("kind") or current["kind"]).strip().lower()
    questions = data.get("questions", current["questions"])
    settings = data.get("settings", current["settings"])
    pages = data.get("pages", current["pages"])
    try:
        forms_engine.validate({"kind": kind, "questions": questions, "settings": settings})
    except forms_engine.FormError as exc:
        raise FormServiceError(str(exc))
    sets = ["name = ?", "kind = ?", "pages = ?", "questions = ?", "settings = ?"]
    values = [str(data.get("name") or current["name"]).strip()[:200], kind,
              json.dumps(pages or []), json.dumps(questions), json.dumps(settings or {})]
    if "status" in data:
        status = str(data.get("status") or "").strip().lower()
        if status not in forms_engine.STATUSES:
            raise FormServiceError("Unknown status: %s" % status)
        sets.append("status = ?")
        values.append(status)
    sets.append("updated_at = ?")
    values.append(now_str())
    values.extend([form_id, org_id])
    with get_connection() as conn:
        conn.execute("UPDATE forms SET %s WHERE id = ? AND org_id = ?" % ", ".join(sets),
                     tuple(values))
    return get_form(form_id, org_id)


def delete_form(form_id, org_id):
    """Archive when answers exist, delete only when the form is genuinely empty.

    Deleting a form someone answered would orphan the answers, and answering a quiz is not
    a throwaway act - so the row stays, out of the way, with its data intact.
    """
    if not get_form(form_id, org_id):
        return None
    with get_connection() as conn:
        used = conn.execute(
            "SELECT COUNT(*) AS n FROM form_responses WHERE form_id = ? AND org_id = ?",
            (form_id, org_id)).fetchone()["n"]
        if used:
            conn.execute("UPDATE forms SET status = 'archived', updated_at = ? "
                         "WHERE id = ? AND org_id = ?", (now_str(), form_id, org_id))
            return {"archived": True, "responses": used}
        conn.execute("DELETE FROM forms WHERE id = ? AND org_id = ?", (form_id, org_id))
    return {"archived": False, "responses": 0}


def _looks_like_name(question):
    """Whether a free-text answer is plausibly a person's name.

    Only a question that says "name" somewhere counts. The first text answer of a quiz is
    usually "thanks for playing", and turning that into a contact would fill a client's CRM
    with nonsense - the reason a submission creates a contact at all is that it carries
    something identifying, not that it carries something.
    """
    ident = " ".join([str(question.get("id") or ""), str(question.get("label") or "")]).lower()
    return "name" in ident


def _contact_from(form, answers, org_id):
    """The contact a submission implies, or None when the form asks for neither.

    ``settings.contact`` maps roles to question ids; without it the first email, phone and
    text answers are used, which is right for the common contact form and harmless for a
    quiz that asks no identifying questions.
    """
    mapping = (form.get("settings") or {}).get("contact") or {}
    picked = {"name": "", "email": "", "phone": ""}
    for role, qid in mapping.items():
        if role in picked and qid:
            value = answers.get(qid)
            if isinstance(value, list):
                value = ", ".join(str(item) for item in value)
            picked[role] = str(value or "").strip()
    for question in forms_engine.question_list(form):
        kind = str(question.get("type") or "text").strip().lower()
        value = answers.get(question["id"])
        if isinstance(value, list):
            value = ", ".join(str(item) for item in value)
        text = str(value or "").strip()
        if kind == "email" and not picked["email"]:
            picked["email"] = text
        elif kind == "phone" and not picked["phone"]:
            picked["phone"] = text
        elif kind == "text" and not picked["name"] and _looks_like_name(question):
            picked["name"] = text
    if not (picked["email"] or picked["name"]):
        return None
    try:
        import contacts
        item = contacts.create_contact(
            dict(picked, notes="Form: %s (#%s)" % (form.get("name") or "", form["id"])),
            org_id, source="form")
        return item["id"]
    except Exception:
        return None      # a broken CRM must never lose the answer itself


def submit(form, answers, ip_hash=None, create_contact=True):
    """Validate, score, store - and only then tell the automations."""
    answers = answers if isinstance(answers, dict) else {}
    problems = forms_engine.validate_answers(form, answers)
    if problems:
        raise FormServiceError(" ".join(problems))
    result = forms_engine.score(form, answers)
    bucket = forms_engine.outcome(form, result)
    org_id = form["org_id"]
    wants_contact = (form.get("settings") or {}).get("create_contact", True)
    contact_id = None
    if create_contact and wants_contact:
        contact_id = _contact_from(form, answers, org_id)
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO form_responses (org_id, form_id, contact_id, answers, score, "
            "max_score, outcome, ip_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (org_id, form["id"], contact_id, json.dumps(answers), result["score"],
             result["max_score"], (bucket or {}).get("label", ""), ip_hash or "", now_str()))
        response_id = cur.lastrowid
    try:
        import automations
        automations.start_for_event(org_id, "form.submitted", context={
            "form.id": form["id"],
            "contact.id": contact_id,
            "form.score": result["score"],
            "form.percent": result["percent"],
        })
    except Exception:
        pass
    return {"response_id": response_id, "contact_id": contact_id,
            "score": result, "outcome": bucket}


def list_responses(form_id, org_id, limit=200):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM form_responses WHERE form_id = ? AND org_id = ? "
            "ORDER BY id DESC LIMIT %d" % max(1, int(limit)), (form_id, org_id)).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        try:
            item["answers"] = json.loads(item.get("answers") or "{}")
        except ValueError:
            item["answers"] = {}
        out.append(item)
    return out


def responses_csv(form, responses):
    """One column per question, in the order the form asks them."""
    import csv
    import io

    questions = forms_engine.question_list(form)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["submitted_at", "score", "max_score", "outcome"]
                    + [str(q.get("label") or q["id"]) for q in questions])
    for row in responses or []:
        answers = row.get("answers") or {}
        cells = []
        for question in questions:
            value = answers.get(question["id"], "")
            if isinstance(value, list):
                value = "; ".join(str(item) for item in value)
            cells.append(value)
        writer.writerow([row.get("created_at"), row.get("score"), row.get("max_score"),
                         row.get("outcome")] + cells)
    return buffer.getvalue()
