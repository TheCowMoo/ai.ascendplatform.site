"""Smoke test for the Server-Sent Events job stream (stage S3).

Run: .\\venv\\Scripts\\python.exe tests\\smoke_sse.py

Creates an ``echo`` job, then reads the SSE stream to completion and checks the
frames: transcript events followed by a terminal ``end`` frame.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

import asgi  # noqa: E402


def main():
    client = TestClient(asgi.app)
    resp = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "admin123"})
    assert resp.status_code == 200, resp.text

    resp = client.post("/api/jobs", json={"prompt": "stream me", "task": "echo"})
    assert resp.status_code == 201, resp.text
    job_id = resp.json()["job"]["id"]
    print("created job", job_id)

    # The route moved to FastAPI, so it now enforces the session dependency.
    anonymous = TestClient(asgi.app)
    assert anonymous.get("/api/jobs/%d/stream" % job_id).status_code == 401, "expected 401 without a session"

    # 404 for an unknown job
    assert client.get("/api/jobs/999999/stream").status_code == 404

    with client.stream("GET", "/api/jobs/%d/stream" % job_id) as stream:
        assert stream.status_code == 200, stream.status_code
        content_type = stream.headers.get("Content-Type", "")
        assert content_type.startswith("text/event-stream"), content_type
        print("content-type:", content_type)
        print("cache-control:", stream.headers.get("Cache-Control"))
        print("x-accel-buffering:", stream.headers.get("X-Accel-Buffering"))
        body = "".join(stream.iter_text())
    frames = [line for line in body.splitlines() if line.startswith(("event:", "data:"))]
    print("frames:", len(frames))
    for line in frames[:8]:
        print("  ", line[:120])

    assert "event: system" in body or "event: message" in body, body[:400]
    assert "event: end" in body, body[-300:]
    end_frame = body.rsplit("event: end", 1)[-1]
    assert "done" in end_frame, end_frame
    print("end frame:", end_frame.strip().replace("\n", " "))
    print("SSE STREAM OK")


if __name__ == "__main__":
    main()
