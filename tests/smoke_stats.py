"""Smoke test for per-bot analytics (stage S2).

Verifies the portable analytics queries that replaced SQLite-only date
functions: ``substr(created_at, 1, 10)`` for grouping and a Python-computed
``days_ago(14)`` cutoff (see db.py). Works on both SQLite and PostgreSQL.

Run: .\\venv\\Scripts\\python.exe tests\\smoke_stats.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402
import app as appmod  # noqa: E402


def main():
    with db.get_connection() as conn:
        row = conn.execute("SELECT id FROM bots ORDER BY created_at LIMIT 1").fetchone()
        bot_id = row["id"]
        for role, content in (
            ("user", "What are your prices?"),
            ("assistant", "Plans start at $99."),
            ("user", "Do you offer support?"),
        ):
            conn.execute(
                "INSERT INTO messages (bot_id, conversation_id, role, content) VALUES (?, ?, ?, ?)",
                (bot_id, "conv-stats-test", role, content),
            )
    print("seeded 3 messages for bot", bot_id)

    client = appmod.app.test_client()
    client.post("/api/auth/login", json={"email": "admin@example.com", "password": "admin123"})
    stats = client.get("/api/bots/%s/stats" % bot_id).get_json()
    print(json.dumps(stats, indent=1))

    assert stats["total_messages"] >= 3, stats
    assert len(stats["messages_per_day"]) >= 1, stats
    assert stats["messages_per_day"][0]["date"], stats
    assert stats["total_conversations"] >= 1, stats
    assert any(q["question"] == "Do you offer support?" for q in stats["top_questions"]), stats
    print("STATS (portable SQL) OK")


if __name__ == "__main__":
    main()
