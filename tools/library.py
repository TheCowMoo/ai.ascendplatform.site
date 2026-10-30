"""The client's uploaded media, for the team that writes emails and pages.

``search_photos`` is stock photography; this is the client's own library. The tool
returns the exact URL and a ready-to-use snippet, including the alt text the uploader
was required to provide. That is what keeps a generated email from shipping an image
with no alternative text, or a URL that only works in the admin console.
"""

import media_library
from db import get_connection

from . import register


def _job_org(job_id):
    if not job_id:
        return None
    with get_connection() as conn:
        row = conn.execute(
            "SELECT org_id FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return row["org_id"] if row else None


def _line(item):
    if item["kind"] == "image":
        size = ""
        if item.get("width") and item.get("height"):
            size = " %dx%d" % (int(item["width"]), int(item["height"]))
        return ("#%d image %s%s — Alt: %s — Description: %s — URL: %s\n"
                "  HTML: %s" % (
                    item["id"], item["original_name"], size, item["alt_text"],
                    item["description"], item["url"], item["site_html"]))
    if item["kind"] == "video":
        return ("#%d video %s — Alt/accessible label: %s — Description: %s — URL: %s\n"
                "  Page HTML: %s\n  Email link: %s" % (
                    item["id"], item["original_name"], item["alt_text"],
                    item["description"], item["url"], item["site_html"],
                    item["email_html"]))
    return ("#%d PDF %s (%s page(s)) — Alt/link text: %s — Description: %s — URL: %s\n"
            "  Link: %s" % (
                item["id"], item["original_name"], item.get("pages") or "?",
                item["alt_text"], item["description"], item["url"],
                item["email_html"]))


def _build_list_media(emit, job_id=None):
    async def list_media(query: str = "") -> str:
        """List this client's uploaded images, video and PDFs with their alt text."""
        org_id = _job_org(job_id)
        if org_id is None:
            return ("This job has no client, so there is no media library to list. "
                    "Pick a client in the workspace switcher first.")
        try:
            rows = media_library.list_media(org_id, query, limit=20)
        except media_library.MediaError as exc:
            return str(exc)
        if not rows:
            return ("No uploaded media yet for this client. Use search_photos for "
                    "stock photography, or ask the user to upload an image, video "
                    "or PDF in the Media tab.")
        base = media_library.public_base()
        lines = ["Uploaded media for this client:"]
        for row in rows:
            lines.append(_line(media_library.out(row, base)))
        if not base:
            lines.append("Set Platform URL in Settings -> Platform email so email "
                         "links are absolute rather than relative.")
        message = "\n".join(lines)
        if emit is not None:
            emit("tool", "tool_result", "list_media: " + str(len(rows)) + " item(s)")
        return message

    return list_media


register(
    "list_media",
    "List the client's uploaded images, video and PDFs with URLs, alt text, "
    "descriptions and ready-to-use HTML. Use this for the client's own media; "
    "use search_photos only for stock photography.",
    _build_list_media,
)
