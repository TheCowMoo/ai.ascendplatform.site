"""Verify job dispatch degrades gracefully when Redis is unreachable.

Run: .\\venv\\Scripts\\python.exe tests\\smoke_backend_fallback.py

Sets JOB_BACKEND=redis with a Redis URL nothing is listening on. The job must
still run (in-process) and the transcript must record the fallback, so a missing
worker never silently drops work.

Environment must be set before importing the app, hence the ordering below.
"""

import os
import sys
import time

os.environ["JOB_BACKEND"] = "redis"
os.environ["REDIS_URL"] = "redis://127.0.0.1:6399/0"  # deliberately unused port

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import jobs  # noqa: E402


def main():
    assert jobs.JOB_BACKEND == "redis", jobs.JOB_BACKEND
    assert jobs.REDIS_URL.endswith("6399/0"), jobs.REDIS_URL
    print("configured backend:", jobs.JOB_BACKEND, "->", jobs.REDIS_URL)

    client = appmod.app.test_client()
    resp = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "admin123"})
    assert resp.status_code == 200, resp.get_data(as_text=True)

    resp = client.post("/api/jobs", json={"prompt": "fallback check", "task": "echo"})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    job_id = resp.get_json()["job"]["id"]
    print("created job", job_id)

    data = None
    for _ in range(60):
        data = client.get("/api/jobs/%d/events?after=0" % job_id).get_json()
        if data["job"]["status"] in ("done", "failed", "cancelled"):
            break
        time.sleep(0.2)

    texts = [event["content"] for event in data["events"]]
    print("status:", data["job"]["status"], "| result:", data["job"]["result"])
    for text in texts:
        print("  -", text[:90])

    assert data["job"]["status"] == "done", data["job"]
    assert any("Redis queue unavailable" in text for text in texts), texts
    print("REDIS FALLBACK OK")


if __name__ == "__main__":
    main()
