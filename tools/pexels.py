"""
Pexels stock-photo tools (stage S6).

Lets the team pull real photography into the content it builds - hero images,
blog art, social cards - instead of only generating them. Three tools:

  * ``search_photos`` - query the Pexels API and return each result's download
    URL, dimensions, photographer credit and page link.
  * ``fetch_photo``   - download one result into ``artifacts/images/``.
  * ``import_photo_to_media`` - download one result into the client's Media
    library, with alt text and credit, and return the local URL/HTML.

Outbound HTTP uses ``urllib``, matching ``meta.py`` and ``app.py`` (no extra
dependency).

Safety: downloads are locked to Pexels image hosts over HTTPS. Without that check
the tool would be a ready-made SSRF probe, since a model could hand it any URL.
Path-confinement applies to artifacts. Searching is read-only; downloading into an
artifact or the client's own Media library is internal and needs no approval gate.

Pexels' licence does not require attribution, but the photographer and page URL
are returned with every result so the content can credit them.
"""

import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request

import actions
import media_library
import settings
from db import get_connection
from tools import policy
from tools import register
from tools import safe_filename

# Structural values decide where output lands, so they are read once at import and
# are marked "restart required" in the Settings tab.
API_BASE = (os.environ.get("PEXELS_API_BASE") or "https://api.pexels.com/v1").rstrip("/")
ARTIFACTS_DIR = (os.environ.get("ARTIFACTS_DIR") or "artifacts").rstrip("/\\")


# The rest are read per call, so a change made in the Settings tab applies to the
# next search or download without restarting the app.
def api_key():
    return settings.get("PEXELS_API_KEY")


def page_size(requested=None):
    """Page size, clamped to the range the API accepts (1-80)."""
    return max(1, min(int(requested or settings.get_int("PEXELS_PER_PAGE", 5)), 80))


def request_timeout():
    return settings.get_int("PEXELS_TIMEOUT", 20)


def max_bytes():
    return settings.get_int("PEXELS_MAX_BYTES", 8000000)

# Cloudflare fronts Pexels and answers the default "Python-urllib/x.y" agent with
# error 1010 (HTTP 403), which is indistinguishable from a rejected API key.
# Identify the app instead - curl- and requests-style agents are accepted too.
USER_AGENT = "AscendPlatform/1.0 (+pexels)"

# Only these hosts may be downloaded from (blocks SSRF via a model-supplied URL).
ALLOWED_IMAGE_HOSTS = ("images.pexels.com",)
ALLOWED_ORIENTATIONS = ("landscape", "portrait", "square")
ALLOWED_SIZES = ("large", "medium", "small")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
PREFERRED_SIZES = ("large", "large2x", "original", "medium", "small")


class PexelsError(Exception):
    """Raised for API, network and URL-validation failures."""


def _http_error_reason(exc):
    """Explain an HTTP failure in terms an operator can act on.

    Pexels sits behind Cloudflare, which answers an unrecognised user agent with
    error 1010 (HTTP 403). That is indistinguishable from a rejected API key, so
    name both possibilities rather than reporting a bare status code.
    """
    if exc.code in (401, 403):
        return (
            "Pexels rejected the request (HTTP %d). Check the API key, and note "
            "that Cloudflare blocks generic urllib user agents - keep a "
            "User-Agent header on the request." % exc.code
        )
    return "HTTP %d from the Pexels API" % exc.code


def check_download_url(url):
    """HTTPS plus a Pexels image host only. Returns a policy-style Decision."""
    parsed = urllib.parse.urlparse(url or "")
    if parsed.scheme != "https":
        return policy.Decision(False, "only https URLs may be downloaded", "deny")
    host = (parsed.hostname or "").lower()
    allowed = any(host == known or host.endswith("." + known) for known in ALLOWED_IMAGE_HOSTS)
    if not allowed:
        return policy.Decision(
            False,
            "refusing to fetch %r: only %s may be downloaded"
            % (host or "?", ", ".join(ALLOWED_IMAGE_HOSTS)),
            "deny",
        )
    return policy.Decision(True, "ok", "read")


def pick_source(src, preferred="large"):
    """Best available variant from a Pexels ``src`` object."""
    if not isinstance(src, dict):
        return ""
    order = [preferred] + [name for name in PREFERRED_SIZES if name != preferred]
    for key in order:
        if src.get(key):
            return src[key]
    return ""


def summarise(photo, preferred="large"):
    """Shape one Pexels photo into the fields the agents actually need."""
    src = photo.get("src") or {}
    photographer = photo.get("photographer") or ""
    page = photo.get("url") or ""
    credit = "Photo by %s on Pexels" % photographer if photographer else "Photo from Pexels"
    if page:
        credit += " (%s)" % page
    return {
        "id": photo.get("id"),
        "alt": photo.get("alt") or "",
        "width": photo.get("width"),
        "height": photo.get("height"),
        "avg_color": photo.get("avg_color") or "",
        "download_url": pick_source(src, preferred),
        "preview_url": src.get("medium") or src.get("small") or "",
        "photographer": photographer,
        "photographer_url": photo.get("photographer_url") or "",
        "page_url": page,
        "credit": credit,
    }


def search(query, per_page=None, orientation=None, size=None, page=1):
    """Call the Pexels search endpoint. Raises PexelsError."""
    query = (query or "").strip()
    if not query:
        raise PexelsError("a search query is required")
    if not api_key():
        raise PexelsError("PEXELS_API_KEY is not set")

    params = {
        "query": query,
        "per_page": page_size(per_page),
        "page": max(1, int(page or 1)),
    }
    if orientation in ALLOWED_ORIENTATIONS:
        params["orientation"] = orientation
    if size in ALLOWED_SIZES:
        params["size"] = size

    url = API_BASE + "/search?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": api_key(),
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=request_timeout()) as response:
            body = response.read(2000000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise PexelsError(_http_error_reason(exc))
    except Exception as exc:
        raise PexelsError("%s: %s" % (type(exc).__name__, exc))
    try:
        return json.loads(body)
    except ValueError as exc:
        raise PexelsError("the API returned invalid JSON (%s)" % exc)


def download(url, path, limit=None):
    """Download an image to ``path``. Raises PexelsError."""
    decision = check_download_url(url)
    if not decision.allowed:
        raise PexelsError(decision.reason)

    cap = limit or max_bytes()
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=request_timeout()) as response:
            data = response.read(cap + 1)
    except urllib.error.HTTPError as exc:
        raise PexelsError(_http_error_reason(exc))
    except Exception as exc:
        raise PexelsError("%s: %s" % (type(exc).__name__, exc))
    if len(data) > cap:
        raise PexelsError("image exceeds the %d byte cap" % cap)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return len(data)


def _destination(job_id, filename):
    name = safe_filename(filename, "photo.jpg", IMAGE_SUFFIXES)
    return os.path.join(ARTIFACTS_DIR, "images", "job-%s" % (job_id or "manual"), name)


def _build_search_photos(emit, job_id=None):
    async def search_photos(query: str, count: int = 5, orientation: str = "landscape") -> str:
        """Search Pexels for stock photos; returns download URLs, credits and dimensions."""
        if not api_key():
            return (
                "PEXELS_API_KEY is not set, so Pexels search is unavailable. "
                "Either generate an image instead, or add a Pexels API key."
            )
        try:
            payload = search(query, per_page=count, orientation=orientation)
        except PexelsError as exc:
            return "Pexels search failed: %s" % exc

        photos = [summarise(photo) for photo in (payload.get("photos") or [])]
        if emit:
            emit("pexels", "system", "Pexels: %d photo(s) for %r" % (len(photos), query))
        result = {"query": query, "total_results": payload.get("total_results", 0), "photos": photos}
        if photos:
            result["next_step"] = (
                "Call import_photo_to_media with download_url, alt, description and credit "
                "to store the chosen photo in this client's Media, then use the URL/HTML it "
                "returns. Do not paste the remote Pexels URL into content."
            )
        return json.dumps(result, indent=1)

    return search_photos


def _build_fetch_photo(emit, job_id=None):
    async def fetch_photo(url: str, filename: str = "photo.jpg") -> str:
        """Download a Pexels image (from search_photos) into artifacts/images/."""
        decision = check_download_url(url)
        if not decision.allowed:
            if emit:
                emit("policy", "error", "Refused to fetch %s: %s" % (url, decision.reason))
            return "Refused: %s" % decision.reason

        path = _destination(job_id, filename)
        path_decision = policy.check_path(path, ARTIFACTS_DIR)
        if not path_decision.allowed:
            return "Refused to save the photo: %s" % path_decision.reason

        authorisation = {"allowed": True, "dry_run": actions.is_dry_run()}
        if not authorisation["allowed"]:
            return "Not downloaded: %s" % authorisation["reason"]
        if authorisation["dry_run"]:
            return "[dry-run] would download %s -> %s" % (url, path)

        try:
            size = download(url, path)
        except PexelsError as exc:
            return "Download failed: %s" % exc
        if emit:
            emit("pexels", "system", "Saved %d bytes to %s" % (size, path))
        # The local copy is for artifacts and records. HTML must use the remote URL:
        # an email client or a published page cannot read this server's disk, and a
        # local path in an <img> is a guaranteed broken image.
        return (
            "Saved %d bytes to %s. For HTML, use the absolute image URL you passed in "
            "(%s), not this local path - recipients cannot read the server's disk."
            % (size, path, url)
        )

    return fetch_photo


register(
    "search_photos",
    "Search Pexels for stock photography; returns download URLs, dimensions and photographer credits.",
    _build_search_photos,
)
register(
    "fetch_photo",
    "Download a Pexels photo (from search_photos) into artifacts/images/.",
    _build_fetch_photo,
)


def _build_import_photo(emit, job_id=None):
    async def import_photo_to_media(url: str, alt: str = "", description: str = "",
                                    credit: str = "", filename: str = "") -> str:
        """Download a Pexels photo into this client's Media library.

        Internal storage, not an outward action: there is no approval gate and no
        dry-run. The returned media URL/HTML is what the team should put in content.
        """
        if not (url or "").strip():
            return "A Pexels download_url is required."
        decision = check_download_url(url)
        if not decision.allowed:
            return "Refused to import the photo: %s" % decision.reason
        org_id, requester = _job_context(job_id)
        if org_id is None:
            return ("I can't put a photo in Media without a client. Pick a client in "
                    "the workspace switcher, then ask again.")
        alt_text = (str(alt or "").strip()[:media_library.MAX_ALT]
                    or "Stock photograph from Pexels")
        note = str(credit or "").strip()
        desc = str(description or "").strip()
        if note and note.lower() not in desc.lower():
            desc = (desc + "\n\n" + note).strip() if desc else note
        if not desc:
            desc = "Stock photograph from Pexels."
        temp_path = ""
        try:
            handle = tempfile.NamedTemporaryFile(
                prefix="pexels-", suffix=".img", delete=False)
            temp_path = handle.name
            handle.close()
            download(url, temp_path)
            name = (os.path.basename(urllib.parse.urlparse(url).path)
                    or filename or "pexels.jpg")
            with open(temp_path, "rb") as stream:
                row = media_library.create_media(
                    _Upload(stream, name), org_id, alt_text,
                    desc[:media_library.MAX_DESCRIPTION], created_by=requester)
            base = media_library.public_base()
            item = media_library.out(row, base)
            result = {
                "media_id": item["id"],
                "url": item["url"],
                "path": item["path"],
                "alt": item["alt_text"],
                "description": item["description"],
                "credit": note,
                "html": item["site_html"],
            }
            if not base:
                result["note"] = (
                    "PLATFORM_URL/ADMIN_DOMAIN is not set, so this URL is relative. "
                    "Set one before this media is used in an email or published page.")
            if emit:
                emit("media", "tool_result",
                     "Pexels photo imported into Media as #%d" % item["id"])
            return json.dumps(result, indent=1)
        except (PexelsError, media_library.MediaError) as exc:
            return "Could not import the Pexels photo into Media: %s" % exc
        except Exception as exc:
            return "Could not import the Pexels photo into Media (%s: %s)" % (
                type(exc).__name__, exc)
        finally:
            if temp_path:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    return import_photo_to_media


class _Upload:
    """What media_library.create_media needs: a filename and a readable stream."""

    def __init__(self, stream, filename):
        self.stream = stream
        self.filename = filename


def _job_context(job_id):
    """(org_id, requester_id) for the job this import belongs to."""
    if not job_id:
        return None, None
    with get_connection() as conn:
        row = conn.execute(
            "SELECT org_id, requester_id FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
    if not row:
        return None, None
    return row["org_id"], row["requester_id"]


register(
    "import_photo_to_media",
    "Download a Pexels photo from search_photos into this client's Media library "
    "and return its local URL, alt text, credit and HTML. Internal; no approval.",
    _build_import_photo,
)