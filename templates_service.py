"""Email templates, and the variable substitution a send step needs.

Kept separate from deliverables: a deliverable is a *draft* someone edits, a template is
*reusable wording* a sequence points at. Merging them would mean every automation pins a
document rather than a template.
"""

import re

from db import get_connection, now_str

PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


class TemplateError(ValueError):
    pass


def render(text, contact=None, extra=None):
    """Substitute {{contact.email}}-style placeholders. Unknown keys are left alone.

    Left alone on purpose: a visible {{contact.phone}} tells the operator the field is
    missing, while an empty string silently sends "call me on ."
    """
    values = dict(extra or {})
    if contact:
        for key, value in (contact or {}).items():
            values["contact." + key] = value
            values[key] = value

    def swap(match):
        key = match.group(1)
        if key not in values or values[key] in (None, ""):
            return match.group(0)
        return str(values[key])

    return PLACEHOLDER.sub(swap, text or "")


def list_templates(org_id, limit=100):
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM email_templates WHERE org_id = ? ORDER BY id DESC "
                            "LIMIT %d" % max(1, int(limit)), (org_id,)).fetchall()
    return [dict(row) for row in rows]


def get_template(template_id, org_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM email_templates WHERE id = ? AND org_id = ?",
                           (template_id, org_id)).fetchone()
    return dict(row) if row else None


def save_template(data, org_id, template_id=None):
    data = data or {}
    name = (data.get("name") or "").strip()
    subject = (data.get("subject") or "").strip()
    body = data.get("body") or ""
    if not name:
        raise TemplateError("A template needs a name.")
    if not subject and not body:
        raise TemplateError("A template needs a subject or a body.")
    with get_connection() as conn:
        if template_id:
            if not get_template(template_id, org_id):
                raise TemplateError("No such template for this client.")
            conn.execute(
                "UPDATE email_templates SET name = ?, subject = ?, body = ?, updated_at = ? "
                "WHERE id = ? AND org_id = ?",
                (name[:200], subject[:300], body, now_str(), template_id, org_id))
            new_id = template_id
        else:
            cur = conn.execute(
                "INSERT INTO email_templates (org_id, name, subject, body, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (org_id, name[:200], subject[:300], body, now_str(), now_str()))
            new_id = cur.lastrowid
        row = conn.execute("SELECT * FROM email_templates WHERE id = ?", (new_id,)).fetchone()
    return dict(row) if row else None


def delete_template(template_id, org_id):
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM email_templates WHERE id = ? AND org_id = ?",
                           (template_id, org_id))
        return cur.rowcount > 0
