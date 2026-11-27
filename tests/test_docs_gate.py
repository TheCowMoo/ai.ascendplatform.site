"""The API schema is opt-in, not an anonymous route inventory.

Run: .\\venv\\Scripts\\python.exe tests\\test_docs_gate.py

`/api/docs` and `/api/openapi.json` describe every route and parameter. They are
useful in development and to an operator, so `API_DOCS=1` still enables them; the
default is no schema and a 404. `smoke_asgi` covers the enabled path.
"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="docs-gate-"), "test.db").replace("\\", "/")
os.environ["SCHEDULER_ENABLED"] = "0"
os.environ.pop("API_DOCS", None)

from fastapi.testclient import TestClient  # noqa: E402

import asgi  # noqa: E402


def test_docs_are_off_by_default():
    assert asgi.docs_enabled() is False
    assert asgi.app.docs_url is None
    assert asgi.app.openapi_url is None
    client = TestClient(asgi.app)
    assert client.get("/api/openapi.json").status_code == 404
    assert client.get("/api/docs").status_code == 404
    print("no schema or Swagger UI without API_DOCS")


def test_the_env_switch_is_explicit():
    original = os.environ.get("API_DOCS")
    try:
        for value, expected in (("1", True), ("true", True), ("0", False),
                                ("no", False), ("", False)):
            os.environ["API_DOCS"] = value
            assert asgi.docs_enabled() is expected, value
    finally:
        if original is None:
            os.environ.pop("API_DOCS", None)
        else:
            os.environ["API_DOCS"] = original
    print("API_DOCS is the only switch that publishes the schema")


if __name__ == "__main__":
    test_docs_are_off_by_default()
    test_the_env_switch_is_explicit()
    print("\nDOCS GATE OK - the schema is opt-in")
