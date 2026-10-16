"""
Business Profile: the single place a business describes itself.

The profile is the source of the text the AI answers from, plus the branding
(colour/logo) and tone of voice applied to bots and the embed widget.
``build_knowledge`` renders it as a deterministic, sectioned text block so what
the model actually sees is predictable and reviewable in the admin UI.
"""

import json
import os

from db import get_connection, now_str, PLATFORM_ORG

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SAMPLE_PATH = os.path.join(BASE_DIR, "knowledge", "cheryl_ai_knowledgebase.txt")

# Plain-text columns the admin form can edit.
TEXT_FIELDS = (
    "name", "tagline", "description", "industry", "website", "logo_url",
    "brand_color", "brand_font", "tone_of_voice", "target_audience",
    "pricing_notes", "support_email", "support_phone", "address",
    "business_hours", "policies", "extra_notes",
)

# Columns stored as JSON.
JSON_FIELDS = ("services", "faq", "social")

DEFAULTS = {
    "name": "", "tagline": "", "description": "", "industry": "", "website": "",
    "logo_url": "", "brand_color": "#4f46e5", "brand_font": "",
    "tone_of_voice": "", "target_audience": "", "services": "[]",
    "pricing_notes": "", "support_email": "", "support_phone": "", "address": "",
    "business_hours": "", "social": "{}", "policies": "", "faq": "[]",
    "extra_notes": "",
}


def _as_dict(row):
    return {k: row[k] for k in row.keys()}


def get_profile(org_id):
    """Return the Business Profile for a client, or None.

    Keyed on the **org**, not the user who created it: the brand belongs to the
    business, so two staff at one client share one profile. Note ``is None`` - org 0
    is the platform's own profile, and ``if not org_id`` would treat it as absent.
    """
    if org_id is None:
        return None
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM business_profiles WHERE org_id = ?", (org_id,)
        ).fetchone()
    return _as_dict(row) if row else None


def ensure_profile(org_id, owner_id=None):
    """Get the org's profile, creating an empty one on first access.

    ``org_id`` None means the login belongs to no client: there is no profile to
    create, and returning None keeps an unattached account from littering NULL-org
    rows that a later client could be handed.
    """
    if org_id is None:
        return None
    existing = get_profile(org_id)
    if existing:
        return existing
    # owner_id records the creator. The column is NOT NULL, and the platform profile
    # is seeded at boot before anyone has logged in, so 0 stands for "no user".
    cols = ["org_id", "owner_id"] + list(DEFAULTS.keys())
    values = [org_id, owner_id if owner_id is not None else 0]
    values += [DEFAULTS[k] for k in DEFAULTS]
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO business_profiles (%s) VALUES (%s)"
            % (", ".join(cols), ", ".join("?" for _ in cols)),
            values,
        )
    return get_profile(org_id)


def update_profile(org_id, data, owner_id=None):
    """Update the editable fields. Returns the updated profile."""
    if ensure_profile(org_id, owner_id) is None:
        return None
    sets, values = [], []
    for field in TEXT_FIELDS:
        if field in data:
            value = data.get(field)
            sets.append("%s = ?" % field)
            values.append(value.strip() if isinstance(value, str) else (value or ""))
    for field in JSON_FIELDS:
        if field in data:
            value = data[field]
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except Exception:
                    value = {} if field == "social" else []
            sets.append("%s = ?" % field)
            values.append(json.dumps(value))
    if not sets:
        return get_profile(org_id)
    sets.append("updated_at = ?")
    values.append(now_str())
    values.append(org_id)
    with get_connection() as conn:
        conn.execute(
            "UPDATE business_profiles SET %s WHERE org_id = ?" % ", ".join(sets),
            values,
        )
    return get_profile(org_id)


def _json_list(raw):
    try:
        value = json.loads(raw or "[]")
    except Exception:
        return []
    return value if isinstance(value, list) else []


def _json_dict(raw):
    try:
        value = json.loads(raw or "{}")
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def build_knowledge(profile):
    """Render a profile as the deterministic KB text handed to the AI.

    Returns "" when there is nothing usable, so callers can concatenate safely.
    """
    if not profile:
        return ""

    out = []

    def add(label, value):
        if isinstance(value, str):
            value = value.strip()
        if value:
            out.append("%s: %s" % (label, value))

    def section(title):
        out.append("")
        out.append("-" * 46)
        out.append(title.upper())
        out.append("-" * 46)

    out.append("========== BUSINESS PROFILE ==========")
    add("Business name", profile.get("name"))
    add("Tagline", profile.get("tagline"))
    add("Industry", profile.get("industry"))
    add("Website", profile.get("website"))

    description = (profile.get("description") or "").strip()
    if description:
        section("About")
        out.append(description)

    services = _json_list(profile.get("services"))
    service_lines = []
    for item in services:
        if isinstance(item, dict):
            name = (item.get("name") or "").strip()
            price = (item.get("price") or "").strip()
            desc = (item.get("description") or "").strip()
            if not (name or desc):
                continue
            line = "- " + (name or "Item")
            if price:
                line += " (%s)" % price
            if desc:
                line += " - " + desc
            service_lines.append(line)
        elif isinstance(item, str) and item.strip():
            service_lines.append("- " + item.strip())
    if service_lines:
        section("Offerings")
        out.extend(service_lines)

    for field, title in (
        ("pricing_notes", "Pricing"),
        ("target_audience", "Target audience"),
        ("tone_of_voice", "Brand voice"),
    ):
        value = (profile.get(field) or "").strip()
        if value:
            section(title)
            out.append(value)

    contact = (
        ("Support email", profile.get("support_email")),
        ("Support phone", profile.get("support_phone")),
        ("Address", profile.get("address")),
        ("Business hours", profile.get("business_hours")),
    )
    if any((value or "").strip() for _, value in contact):
        section("Contact")
        for label, value in contact:
            add(label, value)

    social = _json_dict(profile.get("social"))
    if social:
        section("Social")
        for key, value in social.items():
            add(str(key).title(), value)

    policies = (profile.get("policies") or "").strip()
    if policies:
        section("Policies")
        out.append(policies)

    faq_lines = []
    for item in _json_list(profile.get("faq")):
        if isinstance(item, dict):
            question = (item.get("q") or item.get("question") or "").strip()
            answer = (item.get("a") or item.get("answer") or "").strip()
            if question or answer:
                faq_lines.append("Q: %s\nA: %s" % (question or "-", answer or "-"))
        elif isinstance(item, str) and item.strip():
            faq_lines.append(item.strip())
    if faq_lines:
        section("FAQ")
        out.append("\n\n".join(faq_lines))

    extra = (profile.get("extra_notes") or "").strip()
    if extra:
        section("Additional notes")
        out.append(extra)

    return "\n".join(out).strip()


def sample_knowledge_text():
    """The bundled sample business document (used to seed a first profile)."""
    try:
        if os.path.exists(SAMPLE_PATH):
            with open(SAMPLE_PATH, "r", encoding="utf-8") as handle:
                return handle.read()
    except Exception:
        pass
    return ""


def seed_sample_profile(org_id, owner_id=None):
    """Populate a profile from the bundled sample document."""
    text = sample_knowledge_text()
    data = {
        "name": "Ascend Platform",
        "tagline": "Business growth, managed end to end.",
        "industry": "Business services",
        "tone_of_voice": "Friendly, concise and professional.",
        "website": "https://ascendplatform.site",
    }
    if text:
        data["description"] = text[:8000]
        data["extra_notes"] = text[:2000]
    return update_profile(org_id, data, owner_id)
