"""Uploaded media for a client: images, video and PDFs.

The file bytes live under ``artifacts/media/<org_id>/``; the database row is the
index. A public URL is ``/media/<id>/<uuid>.<ext>`` so a published page or an email
can load it without a session, while the UUID means the id alone is not enough.

Alt text and description are mandatory. They are validated here, not only in the
browser, so an upload cannot be completed by bypassing the form.
"""

import html
import os
import tempfile
import uuid
from urllib.parse import quote

from PIL import Image, ImageOps, UnidentifiedImageError
from pypdf import PdfReader

import settings
from db import get_connection, now_str

# A decompression bomb is a small file that expands to an enormous bitmap. Pillow's
# default limit is generous for a marketing image; this keeps uploads bounded.
Image.MAX_IMAGE_PIXELS = 50_000_000

ARTIFACTS_DIR = (os.environ.get("ARTIFACTS_DIR") or "artifacts").rstrip("/\\")

KINDS = ("image", "video", "document")
IMAGE_FORMATS = {
    "PNG": ("image/png", ".png"),
    "JPEG": ("image/jpeg", ".jpg"),
    "WEBP": ("image/webp", ".webp"),
    "GIF": ("image/gif", ".gif"),
}
MAX_ALT = 300
MAX_DESCRIPTION = 2000


class MediaError(Exception):
    """Invalid media or metadata; the message is safe to show the user."""


class MediaTooLarge(MediaError):
    """The file exceeds the configured upload limit."""


def _as_dict(row):
    return {key: row[key] for key in row.keys()}


def max_bytes():
    """The upload cap, read per request so Settings applies without a restart."""
    value = settings.get_int("MEDIA_MAX_BYTES", 50_000_000)
    return max(1024, value)


def media_root():
    """Where uploaded files live; defaults to artifacts/media."""
    configured = (settings.get("MEDIA_DIR") or "").strip()
    root = configured or os.path.join(ARTIFACTS_DIR, "media")
    if not os.path.isabs(root):
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), root)
    root = os.path.abspath(root)
    os.makedirs(root, exist_ok=True)
    return root


def _org_dir(org_id):
    path = os.path.join(media_root(), str(int(org_id)))
    os.makedirs(path, exist_ok=True)
    return path


def _safe_path(org_id, filename):
    """A path inside this org's directory, or MediaError on traversal."""
    name = os.path.basename(str(filename or ""))
    if not name:
        raise MediaError("The media has no file name.")
    root = os.path.abspath(media_root())
    path = os.path.abspath(os.path.join(root, str(int(org_id)), name))
    if not path.startswith(root + os.sep):
        raise MediaError("That media path is not allowed.")
    return path


def public_base():
    """An absolute base for links used by the agents (emails need one)."""
    base = (settings.get("PLATFORM_URL") or "").strip()
    if not base:
        domain = (settings.get("ADMIN_DOMAIN") or os.environ.get("ADMIN_DOMAIN") or "").strip()
        if domain:
            base = "https://" + domain
    return base.rstrip("/")


def _absolute(path, base_url=None):
    if not base_url:
        return path
    return base_url.rstrip("/") + path


def snippets(row, base_url=None):
    """(site_html, email_html) for one row, with the alt text carried through."""
    path = "/media/%d/%s" % (int(row["id"]), quote(str(row["filename"])))
    url = html.escape(_absolute(path, base_url), quote=True)
    alt = html.escape(str(row.get("alt_text") or ""), quote=True)
    kind = row.get("kind") or "image"

    if kind == "image":
        size = ""
        if row.get("width") and row.get("height"):
            size = ' width="%d" height="%d"' % (int(row["width"]), int(row["height"]))
        tag = '<img src="%s" alt="%s"%s>' % (url, alt, size)
        return tag, tag

    if kind == "video":
        site = ('<video src="%s" controls playsinline preload="metadata" '
                'aria-label="%s">%s</video>' % (url, alt, alt))
        # Video tags are unreliable in email clients; a link is the portable form.
        email = '<a href="%s" target="_blank" rel="noopener">%s</a>' % (url, alt)
        return site, email

    link = '<a href="%s" target="_blank" rel="noopener">%s</a>' % (url, alt)
    return link, link


def out(row, base_url=None):
    """Serialise a row for the API or a tool, including URLs and HTML snippets."""
    item = _as_dict(row)
    item["path"] = "/media/%d/%s" % (int(item["id"]), quote(str(item["filename"])))
    if item.get("thumb_filename"):
        item["thumb_path"] = "/media/%d/thumb/%s" % (
            int(item["id"]), quote(str(item["thumb_filename"])))
    else:
        item["thumb_path"] = ""
    item["url"] = _absolute(item["path"], base_url)
    item["thumb_url"] = _absolute(item["thumb_path"], base_url) if item["thumb_path"] else ""
    item["site_html"], item["email_html"] = snippets(item, base_url)
    for key in ("filename", "thumb_filename", "original_name", "mime",
                "alt_text", "description", "kind", "org_name"):
        item[key] = item.get(key) or ""
    item["bytes"] = int(item.get("bytes") or 0)
    return item


def list_media(org_id=None, query="", kind=None, limit=200):
    """Scoped media rows, newest first, with the client name joined in."""
    sql = ("SELECT m.*, o.name AS org_name FROM media m "
           "LEFT JOIN orgs o ON o.id = m.org_id WHERE 1 = 1")
    params = []
    if org_id is not None:
        sql += " AND m.org_id = ?"
        params.append(org_id)
    if kind:
        if kind not in KINDS:
            raise MediaError("kind must be one of: %s" % ", ".join(KINDS))
        sql += " AND m.kind = ?"
        params.append(kind)
    text = (query or "").strip().lower()
    if text:
        sql += (" AND (LOWER(m.original_name) LIKE ? OR LOWER(m.alt_text) LIKE ? "
                "OR LOWER(m.description) LIKE ?)")
        like = "%" + text + "%"
        params.extend([like, like, like])
    sql += " ORDER BY m.updated_at DESC, m.id DESC LIMIT ?"
    params.append(max(1, int(limit)))
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_as_dict(row) for row in rows]


def get_media(media_id, org_id=None):
    """One media row, optionally constrained to a client."""
    sql = ("SELECT m.*, o.name AS org_name FROM media m "
           "LEFT JOIN orgs o ON o.id = m.org_id WHERE m.id = ?")
    params = [media_id]
    if org_id is not None:
        sql += " AND m.org_id = ?"
        params.append(org_id)
    with get_connection() as conn:
        row = conn.execute(sql, params).fetchone()
    return _as_dict(row) if row else None


def file_path(row):
    return _safe_path(row["org_id"], row["filename"])


def thumb_path(row):
    if not row.get("thumb_filename"):
        return ""
    return _safe_path(row["org_id"], row["thumb_filename"])


def _clean_text(value, label, limit):
    text = str(value or "").replace("\x00", "").strip()
    if not text:
        raise MediaError("%s is required." % label)
    if len(text) > limit:
        raise MediaError("%s must be %d characters or fewer." % (label, limit))
    return text


def _original_name(name):
    value = os.path.basename(str(name or "upload"))
    value = "".join(ch for ch in value if ch >= " " and ch != "\x7f").strip()
    return (value or "upload")[:200]


def _read_upload(file_storage, folder):
    """Stream the upload to a temp file, enforcing the cap before validation."""
    if file_storage is None or not getattr(file_storage, "filename", ""):
        raise MediaError("Choose a file to upload.")
    limit = max_bytes()
    handle = tempfile.NamedTemporaryFile(
        dir=folder, prefix=".upload-", suffix=".tmp", delete=False)
    total = 0
    try:
        stream = file_storage.stream
        try:
            stream.seek(0)
        except Exception:
            pass
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise MediaTooLarge(
                    "This file is larger than the %.1f MB limit." % (limit / 1_000_000.0))
            handle.write(chunk)
        if total == 0:
            raise MediaError("The file is empty.")
    except Exception:
        handle.close()
        try:
            os.remove(handle.name)
        except OSError:
            pass
        raise
    handle.close()
    return handle.name, total


def _sniff(path):
    """Detect (kind, mime, extension, width, height, pages) from the bytes."""
    try:
        with Image.open(path) as probe:
            probe.verify()
        with Image.open(path) as probe:
            fmt = (probe.format or "").upper()
            width, height = probe.size
        if fmt in IMAGE_FORMATS:
            if width * height > Image.MAX_IMAGE_PIXELS:
                raise MediaError("This image is too large to process.")
            mime, ext = IMAGE_FORMATS[fmt]
            return "image", mime, ext, width, height, None
    except MediaError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        pass

    with open(path, "rb") as handle:
        head = handle.read(2048)
    if head.startswith(b"%PDF-") or b"%PDF-" in head[:1024]:
        try:
            reader = PdfReader(path)
            if reader.is_encrypted:
                raise MediaError("Encrypted PDFs are not supported.")
            pages = len(reader.pages)
        except MediaError:
            raise
        except Exception as exc:
            raise MediaError("The PDF could not be read: %s" % exc)
        if pages < 1:
            raise MediaError("The PDF has no pages.")
        return "document", "application/pdf", ".pdf", None, None, pages
    if len(head) >= 12 and head[4:8] == b"ftyp":
        if head[8:12] == b"qt  ":
            raise MediaError("MOV files are not supported; use MP4 or WebM.")
        return "video", "video/mp4", ".mp4", None, None, None
    if head.startswith(b"\x1aE\xdf\xa3"):
        return "video", "video/webm", ".webm", None, None, None
    raise MediaError(
        "Only PNG, JPEG, WebP, GIF, MP4, WebM or PDF files are accepted.")


def _thumbnail(source, destination):
    """A 480px JPEG thumbnail, or False when the image cannot be re-encoded."""
    try:
        with Image.open(source) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail((480, 480), Image.LANCZOS)
            if image.mode in ("RGBA", "LA", "P"):
                rgba = image.convert("RGBA")
                background = Image.new("RGB", rgba.size, (255, 255, 255))
                background.paste(rgba, mask=rgba.split()[-1])
                image = background
            else:
                image = image.convert("RGB")
            image.save(destination, "JPEG", quality=82, optimize=True)
        return True
    except Exception:
        try:
            os.remove(destination)
        except OSError:
            pass
        return False


def create_media(file_storage, org_id, alt_text, description, created_by=None):
    """Validate, store and index one upload. Returns the saved row."""
    alt = _clean_text(alt_text, "Alt text", MAX_ALT)
    desc = _clean_text(description, "Description", MAX_DESCRIPTION)
    try:
        org_id = int(org_id)
    except (TypeError, ValueError):
        raise MediaError("Pick a client before uploading media.")

    folder = _org_dir(org_id)
    temp_path, size = _read_upload(file_storage, folder)
    final_path = ""
    thumb_file = ""
    try:
        kind, mime, ext, width, height, pages = _sniff(temp_path)
        token = uuid.uuid4().hex
        filename = token + ext
        final_path = _safe_path(org_id, filename)
        os.replace(temp_path, final_path)

        thumb_filename = ""
        if kind == "image":
            thumb_filename = token + "-thumb.jpg"
            thumb_file = _safe_path(org_id, thumb_filename)
            if not _thumbnail(final_path, thumb_file):
                thumb_filename = ""
                thumb_file = ""

        with get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO media (org_id, kind, filename, thumb_filename, "
                "original_name, mime, bytes, width, height, pages, alt_text, "
                "description, created_by) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (org_id, kind, filename, thumb_filename,
                 _original_name(getattr(file_storage, "filename", "")),
                 mime, size, width, height, pages, alt, desc, created_by),
            )
            media_id = cur.lastrowid
        row = get_media(media_id)
        if not row:
            raise MediaError("The media could not be saved.")
        return row
    except Exception:
        for path in (temp_path, final_path, thumb_file):
            if path:
                try:
                    os.remove(path)
                except OSError:
                    pass
        raise


def update_media(media_id, data, org_id=None):
    """Edit alt text and description. Both remain mandatory."""
    data = data or {}
    alt = _clean_text(data.get("alt_text"), "Alt text", MAX_ALT)
    desc = _clean_text(data.get("description"), "Description", MAX_DESCRIPTION)
    if not get_media(media_id, org_id):
        raise MediaError("Media not found.")
    with get_connection() as conn:
        conn.execute(
            "UPDATE media SET alt_text = ?, description = ?, updated_at = ? WHERE id = ?",
            (alt, desc, now_str(), media_id),
        )
    return get_media(media_id, org_id)


def delete_media(media_id, org_id=None):
    """Delete the row first, then the files; returns False when it was not found."""
    row = get_media(media_id, org_id)
    if not row:
        return False
    with get_connection() as conn:
        conn.execute("DELETE FROM media WHERE id = ?", (media_id,))
    for path in (file_path(row), thumb_path(row)):
        if not path:
            continue
        try:
            os.remove(path)
        except OSError:
            pass
    return True



