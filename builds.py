"""Guided build sessions: server-side state for the plain-language interview.

The guide itself is pure (``build_guides``); this module is the small persistence layer
that makes an unfinished website, form, blog post or newsletter survive a reload and
belong to exactly one client.
"""

import json

import build_guides
from db import get_connection, now_str

STATUSES = ("asking", "ready", "building", "done", "error", "cancelled")


class BuildError(ValueError):
    pass


def _as_dict(row):
    return {k: row[k] for k in row.keys()}


def _json(raw, fallback):
    try:
        value = json.loads(raw or "")
    except (TypeError, ValueError):
        return fallback
    return value if isinstance(value, type(fallback)) else fallback


def _row(build_id, org_id=None):
    query = "SELECT * FROM build_sessions WHERE id = ?"
    params = [build_id]
    if org_id is not None:
        query += " AND org_id = ?"
        params.append(org_id)
    with get_connection() as conn:
        row = conn.execute(query, tuple(params)).fetchone()
    return _as_dict(row) if row else None


def get_by_job(job_id):
    if not job_id:
        return None
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM build_sessions WHERE job_id = ? ORDER BY id DESC LIMIT 1",
            (int(job_id),)).fetchone()
    return _as_dict(row) if row else None


def create(org_id, user_id, kind, seed=None, title=None):
    kind = build_guides.normalise_kind(kind)
    if not kind:
        raise BuildError("Choose a website, blog post, form or newsletter.")
    if org_id is None:
        raise BuildError("Pick a client before starting a build.")
    answers = dict(seed or {})
    title = (title or build_guides.title_for(kind, answers))[:200]
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO build_sessions (org_id, user_id, kind, title, status, step, "
            "answers_json, result_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'asking', 0, ?, '{}', ?, ?)",
            (org_id, user_id, kind, title, json.dumps(answers), now_str(), now_str()))
        build_id = cur.lastrowid
    return out(_row(build_id, org_id))


def out(row, step=None):
    """The console payload for a build. ``step`` is for going back to edit."""
    if not row:
        return None
    kind = row.get("kind") or "website"
    answers = _json(row.get("answers_json"), {})
    result = _json(row.get("result_json"), {})
    status = row.get("status") or "asking"
    current = row.get("step") or 0
    if step is not None:
        try:
            current = max(0, int(step))
        except (TypeError, ValueError):
            current = row.get("step") or 0
        if current < len(build_guides.STEPS.get(kind, ())):
            status = "asking"
    payload = build_guides.payload(kind, answers, current, status, result,
                                   build_id=row.get("id") or 0)
    payload.update({
        "id": row.get("id") or 0,
        "job_id": row.get("job_id"),
        "error": row.get("error") or "",
        "created_at": row.get("created_at") or "",
        "updated_at": row.get("updated_at") or "",
    })
    if status == "done":
        payload["summary"] = build_guides.summary(kind, answers)
    return payload


def get(build_id, org_id, step=None):
    return out(_row(build_id, org_id), step=step)


def list_for_org(org_id, limit=20):
    if org_id is None:
        return []
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM build_sessions WHERE org_id = ? "
            "ORDER BY id DESC LIMIT %d" % max(1, int(limit)), (org_id,)).fetchall()
    out_rows = []
    for row in rows:
        item = _as_dict(row)
        kind = item.get("kind") or "website"
        total = len(build_guides.STEPS.get(kind, ()))
        current = int(item.get("step") or 0)
        out_rows.append({
            "id": item["id"],
            "kind": kind,
            "title": item.get("title") or "",
            "status": item.get("status") or "asking",
            "step": min(max(0, current), max(0, total - 1)),
            "total_steps": total,
            "updated_at": item.get("updated_at") or "",
        })
    return out_rows


def save_answers(build_id, org_id, step, answers):
    """Validate one step, save it, and return ``(build, errors)``."""
    row = _row(build_id, org_id)
    if not row:
        raise BuildError("That build was not found.")
    if row.get("status") in ("building", "done", "cancelled"):
        raise BuildError("That build is already finished.")
    kind = row.get("kind") or "website"
    existing = _json(row["answers_json"], {})
    allowed = {question["id"] for question in build_guides.QUESTIONS.get(kind, [])}
    # The submitted step is merged over the saved answers, so a conditional question
    # (for example "what is the web address?" after "yes") sees the earlier answer.
    supplied = {key: value for key, value in dict(answers or {}).items() if key in allowed}
    merged_input = {key: value for key, value in existing.items() if key in allowed}
    merged_input.update(supplied)
    merged, errors = build_guides.validate_step(kind, step, merged_input)
    if errors:
        return None, errors
    total = len(build_guides.STEPS.get(kind, ()))
    next_step = build_guides.advance(kind, merged, step)
    status = "ready" if next_step >= total else "asking"
    with get_connection() as conn:
        conn.execute(
            "UPDATE build_sessions SET answers_json = ?, step = ?, status = ?, "
            "title = ?, error = '', updated_at = ? WHERE id = ? AND org_id = ?",
            (json.dumps(merged), next_step, status,
             build_guides.title_for(kind, merged)[:200], now_str(), build_id, org_id))
    return out(_row(build_id, org_id)), []


def mark_building(build_id, job_id):
    with get_connection() as conn:
        conn.execute(
            "UPDATE build_sessions SET status = 'building', job_id = ?, error = '', "
            "updated_at = ? WHERE id = ?", (job_id, now_str(), build_id))


def finish(build_id, result):
    with get_connection() as conn:
        conn.execute(
            "UPDATE build_sessions SET status = 'done', result_json = ?, error = '', "
            "updated_at = ? WHERE id = ?",
            (json.dumps(result or {}), now_str(), build_id))


def fail(build_id, error):
    with get_connection() as conn:
        conn.execute(
            "UPDATE build_sessions SET status = 'error', error = ?, updated_at = ? "
            "WHERE id = ?", (str(error or "")[:2000], now_str(), build_id))


def cancel(build_id, org_id):
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE build_sessions SET status = 'cancelled', updated_at = ? "
            "WHERE id = ? AND org_id = ? AND status NOT IN ('building', 'done')",
            (now_str(), build_id, org_id))
        return cur.rowcount > 0
