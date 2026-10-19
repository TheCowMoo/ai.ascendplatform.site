"""Smoke test for the ASGI composition (stage S4).

Run: .\\venv\\Scripts\\python.exe tests\\smoke_asgi.py

FastAPI now owns some routes while Flask still serves the rest, mounted at "/".
This proves BOTH halves work through one ASGI app, that FastAPI takes
precedence, and that the shared session cookie authenticates across the boundary.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

import asgi  # noqa: E402


def main():
    client = TestClient(asgi.app)

    # --- FastAPI-owned route, operator only ---------------------------------
    # The agents are site-wide and run on the platform's keys, so the provider list is
    # operator information. It used to answer with no session at all. The payload shape
    # and the operator's 200 are covered by smoke_tenancy, which has owner_login().
    anon = TestClient(asgi.app)
    assert anon.get("/api/providers").status_code == 401, "providers answered anonymously"
    print("providers: anonymous request -> 401")
    # The provider payload itself (names, configured, gateway) is asserted by
    # smoke_llm_gateway and by smoke_tenancy's owner check.

    # --- Flask route, still served through the ASGI mount ----------------
    health = client.get("/api/health")
    assert health.status_code == 200, health.text
    print("health via mount:", {k: health.json()[k] for k in ("status", "brand", "job_backend")})
    assert health.json()["brand"] == "AscendPlatform"
    assert health.json()["job_backend"] == "local"

    # FastAPI's own OpenAPI surface is reachable and precedes the mount.
    docs = client.get("/api/openapi.json")
    assert docs.status_code == 200, docs.status_code
    paths = docs.json()["paths"]
    print("openapi paths:", sorted(paths))
    assert "/api/jobs/{job_id}/stream" in paths
    assert "/api/providers" in paths

    # --- Session bridge --------------------------------------------------
    anonymous = TestClient(asgi.app)
    assert anonymous.get("/api/jobs/1/stream").status_code == 401, "FastAPI route must require a session"

    login = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "admin123"})
    assert login.status_code == 200, login.text
    print("logged in:", login.json()["user"]["email"])

    # The same cookie now authenticates a Flask route through the mount...
    bots = client.get("/api/bots")
    assert bots.status_code == 200, bots.text
    print("bots via mount after login:", len(bots.json()["bots"]))

    # ...and a FastAPI route.
    profile = client.get("/api/profile")
    assert profile.status_code == 200, profile.text
    print("profile name (Flask):", profile.json()["profile"]["name"])

    print("ASGI COMPOSITION OK")


if __name__ == "__main__":
    main()
