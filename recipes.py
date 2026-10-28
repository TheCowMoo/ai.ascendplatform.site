"""Recipes: the safe half of agent-written tools.

A recipe is data, not code. It is validated against a closed verb set before it can be
approved, and executed by an interpreter with no eval, no imports and no filesystem. An
HTTP call may only reach hosts the recipe declares in its own ``allow`` list - that list is
what a human is actually approving on the review screen.
"""

import json
import re

VERBS = ("http_request", "render_template", "tag_contact", "notify", "if")
MAX_STEPS = 20
TEMPLATE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


class RecipeError(ValueError):
    pass


def parse(body):
    """(steps, allow) from a stored body. Raises RecipeError on anything unexpected."""
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError as exc:
            raise RecipeError("The body is not valid JSON: %s" % exc)
    if isinstance(body, dict):
        steps = body.get("steps")
        allow = body.get("allow") or []
    else:
        steps, allow = body, []
    if not isinstance(steps, list) or not steps:
        raise RecipeError("A recipe needs a non-empty list of steps.")
    if len(steps) > MAX_STEPS:
        raise RecipeError("Too many steps (%d); the limit is %d." % (len(steps), MAX_STEPS))
    if not isinstance(allow, list) or any(not isinstance(h, str) for h in allow):
        raise RecipeError("allow must be a list of host names.")
    return steps, [h.strip().lower() for h in allow if h.strip()]


def validate(body):
    """Every complaint about a recipe, so a model can fix them in one pass."""
    problems = []
    try:
        steps, allow = parse(body)
    except RecipeError as exc:
        return [str(exc)]
    for index, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            problems.append("Step %d is not an object." % index)
            continue
        verb = step.get("do")
        if verb not in VERBS:
            problems.append("Step %d uses unknown verb %r." % (index, verb))
            continue
        if verb == "http_request":
            url = str(step.get("url") or "")
            if not url.startswith("https://"):
                problems.append("Step %d: http_request needs an https:// url." % index)
                continue
            host = url.split("/")[2].split(":")[0].lower()
            if host not in allow:
                problems.append("Step %d calls %s, which is not in allow." % (index, host))
            if str(step.get("method") or "POST").upper() not in ("GET", "POST"):
                problems.append("Step %d: method must be GET or POST." % index)
        if verb == "render_template" and not str(step.get("template") or "").strip():
            problems.append("Step %d: render_template needs a template." % index)
        if verb == "tag_contact" and not str(step.get("tags") or "").strip():
            problems.append("Step %d: tag_contact needs tags." % index)
        if verb == "notify" and not str(step.get("message") or "").strip():
            problems.append("Step %d: notify needs a message." % index)
        if verb == "if" and not str(step.get("field") or "").strip():
            problems.append("Step %d: if needs a field to test." % index)
    return problems


def fill(text, values):
    """Substitute {{a.b}} keys. Unknown keys stay visible, as everywhere else here."""
    def swap(match):
        value = values.get(match.group(1))
        return match.group(0) if value in (None, "") else str(value)

    return TEMPLATE.sub(swap, str(text or ""))


def run(body, args=None, context=None, timeout=10):
    """Execute a recipe. Returns [(where, ok, detail)]; a step failure never raises."""
    import contacts as contacts_service

    steps, allow = parse(body)
    values = dict(context or {})
    values.update({"args." + key: value for key, value in (args or {}).items()})
    org_id = values.get("org_id")
    results = []

    for index, step in enumerate(steps, 1):
        verb = step.get("do")
        where = "step %d (%s)" % (index, verb)

        if verb == "render_template":
            results.append((where, True, fill(step.get("template"), values)))
            continue

        if verb == "notify":
            results.append((where, True, fill(step.get("message"), values)))
            continue

        if verb == "tag_contact":
            contact_id = step.get("contact_id") or values.get("contact.id")
            try:
                cid = int(str(contact_id).strip())
            except (TypeError, ValueError):
                results.append((where, False, "no contact id"))
                continue
            contact = contacts_service.get_contact(cid, org_id) if org_id else None
            if not contact:
                results.append((where, False, "contact %s not found for this client" % cid))
                continue
            wanted = [tag.strip() for tag in fill(step.get("tags"), values).split(",")
                      if tag.strip()]
            merged = list(dict.fromkeys((contact.get("tags") or []) + wanted))
            contacts_service.update_contact(cid, org_id, {"tags": merged})
            results.append((where, True, "tags: " + ", ".join(merged)))
            continue

        if verb == "if":
            left = fill("{{%s}}" % step.get("field"), values)
            right = fill(step.get("equals"), values)
            if left == right:
                results.append((where, True, "matched"))
                continue
            # A false test ends the recipe: recipes are a straight line, and pretending
            # otherwise without a documented control-flow model would be worse than this.
            results.append((where, True, "skipped - stopping here"))
            break

        if verb == "http_request":
            import urllib.request

            url = fill(step.get("url"), values)
            host = url.split("/")[2].split(":")[0].lower() if url.startswith("https://") else ""
            if host not in allow:
                results.append((where, False, "host not allowed: %s" % (host or url)))
                continue
            payload = json.dumps(step.get("json")).encode() if step.get("json") is not None else None
            try:
                request = urllib.request.Request(
                    url, data=payload, headers={"Content-Type": "application/json"},
                    method=str(step.get("method") or "POST").upper())
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    results.append((where, True, "HTTP %d" % response.status))
            except Exception as exc:
                results.append((where, False, "%s: %s" % (type(exc).__name__, str(exc)[:200])))
            continue

        results.append((where, False, "unknown verb"))
    return results

