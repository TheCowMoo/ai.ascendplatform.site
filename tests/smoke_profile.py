"""Smoke test for the Business Profile (Stage 1).

Run from the repo root:
    .\\venv\\Scripts\\python.exe tests\\smoke_profile.py

Covers: profile create-on-read, update, the generated knowledge base the AI
actually receives, and widget branding inheritance for a new bot.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402


def main():
    client = appmod.app.test_client()

    resp = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "admin123"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    print("logged in as", resp.get_json()["user"]["email"])

    # Read (create-on-read; seeded on first run).
    resp = client.get("/api/profile")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    print("profile name after seed:", resp.get_json()["profile"]["name"])

    payload = {
        "name": "Nathanael Consulting",
        "tagline": "Practical growth for small teams.",
        "description": "We help small businesses grow with automation and AI.",
        "brand_color": "#0ea5e9",
        "tone_of_voice": "Direct, warm and jargon-free.",
        "services": [
            {"name": "AI Chatbot Setup", "price": "$499", "description": "Deploy a trained assistant"},
            {"name": "Website Build", "price": "$999", "description": "Landing page and hosting"},
        ],
        "faq": [{"q": "Do you offer support?", "a": "Yes, Monday to Friday, 9-5."}],
        "social": {"facebook": "https://facebook.com/nc", "instagram": "https://instagram.com/nc"},
        "support_email": "hello@nathanael.dev",
    }
    resp = client.put("/api/profile", json=payload)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    prof = resp.get_json()["profile"]
    assert prof["name"] == payload["name"], prof["name"]
    assert len(prof["services"]) == 2, prof["services"]
    assert prof["social"]["facebook"].endswith("/nc")
    print("updated profile ok | brand_color:", prof["brand_color"])

    resp = client.get("/api/profile/knowledge")
    knowledge = resp.get_json()["knowledge"]
    print("knowledge chars:", len(knowledge))
    for needle in (
        "Nathanael Consulting",
        "AI Chatbot Setup",
        "$999",
        "BRAND VOICE",
        "FAQ",
        "Do you offer support?",
        "hello@nathanael.dev",
    ):
        assert needle in knowledge, "missing from KB: %s" % needle
    print("--- knowledge preview ---")
    print("\n".join(knowledge.splitlines()[:14]))

    # A NEW bot (no brand colour) inherits the profile's branding.
    resp = client.post("/api/bots", json={"name": "Inheriting Bot"})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    bot = resp.get_json()["bot"]
    assert bot["use_profile_kb"] is True, bot
    assert bot["profile_id"] == prof["id"], (bot["profile_id"], prof["id"])
    print("new bot profile_id:", bot["profile_id"], "| use_profile_kb:", bot["use_profile_kb"])

    resp = client.get("/api/bots/%s/public" % bot["id"])
    pub = resp.get_json()
    print("public bot brand_color:", pub["brand_color"])
    assert pub["brand_color"] == "#0ea5e9", pub["brand_color"]

    print("STAGE 1 PROFILE OK")


if __name__ == "__main__":
    main()
