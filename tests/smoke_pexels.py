"""Pexels tool tests (stage S6).

Run: .\\venv\\Scripts\\python.exe tests\\smoke_pexels.py

No API key is needed and nothing is downloaded: the URL allowlist, result
shaping, the dry-run gate, the request headers and the failure paths are all
exercised offline. The one network call uses a deliberately invalid key and only
asserts that the error is reported gracefully, so it also passes with no
connectivity.

Configuration goes through the Settings layer (the same path the admin tab uses)
rather than module globals, which doubles as proof that a change applies without
restarting anything.
"""

import asyncio
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Deterministic environment, set BEFORE importing the modules.
os.environ.pop("ALLOW_LIVE_ACTIONS", None)
os.environ.pop("PEXELS_API_KEY", None)
os.environ["ARTIFACTS_DIR"] = "artifacts"

import db  # noqa: E402
import settings  # noqa: E402
import tools.pexels as pexels  # noqa: E402
from tools import safe_filename  # noqa: E402

db.init_db()


def test_url_allowlist():
    """Only https on a Pexels image host may be fetched (blocks SSRF)."""
    allowed = [
        "https://images.pexels.com/photos/1234/nature.jpg",
        "https://images.pexels.com/photos/1234/nature.jpeg?auto=compress&w=1200",
    ]
    blocked = [
        "http://images.pexels.com/photos/1/x.jpg",       # not https
        "https://169.254.169.254/latest/meta-data/",     # cloud metadata
        "https://127.0.0.1:8000/api/health",             # localhost
        "https://api.pexels.com/v1/search?query=x",      # API host, not images
        "https://evil.com/images.pexels.com/x.jpg",      # hostname trick
        "file:///etc/passwd",
        "",
    ]
    for url in allowed:
        decision = pexels.check_download_url(url)
        assert decision.allowed, (url, decision)
        print("  allow", url[:70])
    for url in blocked:
        decision = pexels.check_download_url(url)
        assert not decision.allowed, (url, decision)
        print("  deny ", (url or "(empty)")[:58], "->", decision.reason[:46])


def test_result_shaping():
    payload = {
        "id": 42,
        "width": 1920,
        "height": 1280,
        "url": "https://www.pexels.com/photo/blue-42/",
        "photographer": "Ada Lovelace",
        "photographer_url": "https://www.pexels.com/@ada",
        "avg_color": "#3355aa",
        "alt": "A blue abstract",
        "src": {
            "original": "o.jpg", "large2x": "l2.jpg", "large": "l.jpg",
            "medium": "m.jpg", "small": "s.jpg", "tiny": "t.jpg",
        },
    }
    out = pexels.summarise(payload)
    print(json.dumps(out, indent=1))
    assert out["download_url"] == "l.jpg"
    assert out["preview_url"] == "m.jpg"
    assert out["photographer"] == "Ada Lovelace"
    assert "Ada Lovelace" in out["credit"] and "pexels.com" in out["credit"]

    assert pexels.pick_source({"medium": "m.jpg"}) == "m.jpg"
    assert pexels.pick_source({}) == ""
    print("result shaping OK")


def test_search_requires_a_key():
    assert pexels.api_key() == "", "this test expects no key to be configured"
    try:
        pexels.search("mountains")
    except pexels.PexelsError as exc:
        print("search without a key ->", exc)
    else:
        raise AssertionError("expected PexelsError without an API key")


def test_search_tool_without_key():
    tool = pexels._build_search_photos(None, None)
    result = asyncio.run(tool("mountains", 3))
    print("tool without key ->", result)
    assert "PEXELS_API_KEY is not set" in result, result


def test_search_tool_reports_api_errors():
    """A bad key must give a graceful message, not a traceback.

    The key is set through Settings, so this also covers the panel path: no
    restart, no module reload.
    """
    settings.set_value("PEXELS_API_KEY", "deliberately-invalid-key", "test")
    try:
        result = asyncio.run(pexels._build_search_photos(None, None)("mountains"))
    finally:
        settings.clear_value("PEXELS_API_KEY", "test")
    print("tool with bad key ->", result[:130])
    assert result.startswith("Pexels search failed:"), result
    assert pexels.api_key() == "", "the key should be gone again"


def test_settings_drive_the_tools():
    """Configuration comes from Settings, so a change applies with no restart."""
    settings.set_value("PEXELS_PER_PAGE", "12", "test")
    settings.set_value("PEXELS_TIMEOUT", "33", "test")
    try:
        print("panel config -> page_size=%d timeout=%d"
              % (pexels.page_size(), pexels.request_timeout()))
        assert pexels.page_size() == 12, pexels.page_size()
        assert pexels.request_timeout() == 33, pexels.request_timeout()
    finally:
        settings.clear_value("PEXELS_PER_PAGE", "test")
        settings.clear_value("PEXELS_TIMEOUT", "test")
    assert pexels.page_size() == 5, "should fall back to the SPEC default"
    print("defaults restored ->", pexels.page_size())


def test_every_request_identifies_the_app():
    """Cloudflare answers the default urllib agent with 403 (error 1010).

    That is indistinguishable from a rejected API key, so dropping the
    User-Agent header would break every search while pointing at the wrong cause.
    """
    seen = []
    original = urllib.request.urlopen

    def recorder(request, *args, **kwargs):
        seen.append(request)
        raise OSError("network disabled in this test")

    settings.set_value("PEXELS_API_KEY", "dummy-key-for-header-check", "test")
    urllib.request.urlopen = recorder
    try:
        for label, call in (
            ("search", lambda: pexels.search("x")),
            ("download", lambda: pexels.download(
                "https://images.pexels.com/photos/1/x.jpg",
                "artifacts/never-written.jpg")),
        ):
            try:
                call()
            except pexels.PexelsError:
                pass
            else:
                raise AssertionError("%s should have raised" % label)
            agent = seen[-1].get_header("User-agent")
            assert agent and "AscendPlatform" in agent, (label, agent)
            print("  %-9s User-Agent: %s" % (label, agent))
    finally:
        urllib.request.urlopen = original
        settings.clear_value("PEXELS_API_KEY", "test")

    assert len(seen) == 2, seen
    assert not os.path.exists("artifacts/never-written.jpg"), "a refused download wrote a file"
    print("both requests identified the app; nothing written to disk")


def test_fetch_is_gated_and_confined():
    good = "https://images.pexels.com/photos/9/hero.jpg"

    refused = asyncio.run(pexels._build_fetch_photo(None, 7)("http://169.254.169.254/latest/meta-data/", "x.jpg"))
    assert refused.startswith("Refused:"), refused
    print("ssrf refused ->", refused[:78])

    simulated = asyncio.run(pexels._build_fetch_photo(None, 7)(good, "hero.jpg"))
    print("dry-run ->", simulated)
    assert simulated.startswith("[dry-run] would download"), simulated
    assert "job-7/hero.jpg" in simulated.replace("\\", "/"), simulated

    traversal = asyncio.run(pexels._build_fetch_photo(None, 7)(good, "../../../../etc/passwd"))
    assert "passwd" in traversal and ".." not in traversal, traversal
    print("traversal contained ->", traversal[-58:])


def test_safe_filename():
    assert safe_filename("../../etc/passwd", "photo.jpg", (".jpg",)) == "passwd.jpg"
    assert safe_filename("", "photo.jpg", (".jpg",)) == "photo.jpg"
    assert safe_filename("noext", "photo.jpg", (".jpg",)) == "noext.jpg"
    print("safe_filename OK")


if __name__ == "__main__":
    # Some checks need a clean slate (no key, the default page size). Save whatever
    # is configured, clear it for the run, then put it back - so this suite is safe
    # to run on a machine that has real values stored in the Settings tab.
    # Only values actually stored in the panel matter: settings.get() also returns
    # SPEC defaults, which are not ours to save and restore.
    saved = {}
    for key in ("PEXELS_API_KEY", "PEXELS_PER_PAGE"):
        if settings.source_of(key) == "panel":
            saved[key] = settings.get(key)
            settings.clear_value(key, "smoke-setup")
    settings.forget()
    try:
        test_url_allowlist()
        test_result_shaping()
        test_search_requires_a_key()
        test_search_tool_without_key()
        test_search_tool_reports_api_errors()
        test_settings_drive_the_tools()
        test_every_request_identifies_the_app()
        test_fetch_is_gated_and_confined()
        test_safe_filename()
    finally:
        settings.forget()
        for key, value in saved.items():
            settings.set_value(key, value, "smoke-restore")
        settings.forget()
    print("PEXELS OK")