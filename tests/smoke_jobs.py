"""Smoke test for the job engine (Step 2).

Run from the repo root with the venv interpreter:
    .\\venv\\Scripts\\python.exe tests\\smoke_jobs.py

Logs in as the seeded admin, creates an ``echo`` job, polls its event stream,
and asserts the job reaches ``done``. Requires no API keys.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402


def main():
    client = appmod.app.test_client()

    resp = client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": "admin123"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    print("logged in as", resp.get_json()["user"]["email"])

    resp = client.post("/api/jobs", json={"prompt": "say hello three times", "task": "echo"})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    job = resp.get_json()["job"]
    job_id = job["id"]
    print("created job", job_id, "status =", job["status"])

    data = None
    for _ in range(50):
        resp = client.get("/api/jobs/%d/events?after=0" % job_id)
        data = resp.get_json()
        if data["job"]["status"] in ("done", "failed", "cancelled"):
            break
        time.sleep(0.1)

    print("final status:", data["job"]["status"], "| result:", data["job"]["result"])
    for event in data["events"]:
        print("  [%s] %s: %s" % (event["seq"], event["source"], event["content"]))

    assert data["job"]["status"] == "done", data["job"]
    print("STEP 2 OK")


if __name__ == "__main__":
    main()
