"""The Media tab: uploads that cannot finish without alt text and a description.

Run: .\\venv\\Scripts\\python.exe tests\\smoke_media.py

What is pinned here is the promise: an image, video or PDF is saved only when both
fields are present, it belongs to the selected client, the public URL serves the real
bytes, and the team's list_media tool carries the alt text into the HTML it returns.
"""

import asyncio
import io
import os
import shutil
import sys
import tempfile
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Isolate the test files, and keep the upload limit small enough to exercise it.
MEDIA_DIR = tempfile.mkdtemp(prefix="ascend-media-test-")
os.environ["MEDIA_DIR"] = MEDIA_DIR
os.environ["MEDIA_MAX_BYTES"] = "10000"

from PIL import Image  # noqa: E402
from pypdf import PdfWriter  # noqa: E402

from autogen_core import CancellationToken  # noqa: E402

import app as appmod  # noqa: E402
import db  # noqa: E402
import jobs  # noqa: E402
import media_library  # noqa: E402
import orgs as orgs_service  # noqa: E402
import tools as tool_registry  # noqa: E402

FAILED = []
TOKEN = uuid.uuid4().hex[:6]


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name +
          ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


def _org(name):
    return orgs_service.create_org({"name": "%s %s" % (name, TOKEN)})


def _login():
    client = appmod.app.test_client()
    resp = client.post("/api/auth/login",
                       json={"email": "admin@example.com", "password": "admin123"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return client


def _pick(client, org_id):
    resp = client.post("/api/session/org", json={"org_id": org_id})
    assert resp.status_code == 200, resp.get_data(as_text=True)


def _png():
    buffer = io.BytesIO()
    Image.new("RGB", (16, 12), (200, 30, 30)).save(buffer, "PNG")
    return buffer.getvalue()


def _pdf():
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _video():
    # A minimal MP4 header. The validator checks the container signature; playback
    # is left to the browser.
    return b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 32


def _upload(client, data, name, alt, description):
    return client.post(
        "/api/media",
        data={"file": (io.BytesIO(data), name),
              "alt_text": alt, "description": description},
        content_type="multipart/form-data",
    )


def _count(org_id):
    with db.get_connection() as conn:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM media WHERE org_id = ?", (org_id,)).fetchone()["n"]


def main():
    org = _org("Media Client")
    other = _org("Media Other")
    client = _login()
    _pick(client, org["id"])

    # 1. The mandatory fields come first: no file, no row.
    before = _count(org["id"])
    missing = _upload(client, _png(), "no-alt.png", "   ", "description")
    check("alt text is required", missing.status_code, 400)
    check("the refusal names alt text", "Alt text is required" in
          (missing.get_json() or {}).get("error", ""), True)
    missing = _upload(client, _png(), "no-desc.png", "alt", "")
    check("description is required", missing.status_code, 400)
    check("the refusal names description", "Description is required" in
          (missing.get_json() or {}).get("error", ""), True)
    check("nothing was written for a refused upload", _count(org["id"]), before)

    # 2. A real image is stored, served and indexed.
    made = _upload(client, _png(), "red.png", "A red rectangle", "Test image")
    check("an image upload is accepted", made.status_code, 201)
    image = (made.get_json() or {}).get("media") or {}
    check("the image is an image", image.get("kind"), "image")
    check("dimensions survive", (image.get("width"), image.get("height")), (16, 12))
    check("alt text survives", image.get("alt_text"), "A red rectangle")
    check("the URL carries the alt text",
          'alt="A red rectangle"' in image.get("site_html", ""), True)
    check("the file exists", os.path.isfile(media_library.file_path(image)), True)
    check("the thumbnail exists", os.path.isfile(media_library.thumb_path(image)), True)

    served = client.get(image.get("path"))
    check("the public path serves the image", served.status_code, 200)
    check("the image keeps its MIME type", served.headers.get("Content-Type"), "image/png")
    check("the served bytes are the upload", served.data, _png())
    thumb = client.get(image.get("thumb_path"))
    check("the thumbnail is served", thumb.status_code, 200)
    check("the thumbnail is JPEG", thumb.headers.get("Content-Type"), "image/jpeg")
    # Windows keeps a file locked while a response handle is open; close the public
    # responses before the delete test at the end.
    served.close()
    thumb.close()


    # 3. PDF: validated by pypdf, page count stored, download rather than inline.
    made = _upload(client, _pdf(), "brief.pdf", "The brief", "A one-page brief")
    check("a PDF upload is accepted", made.status_code, 201)
    document = (made.get_json() or {}).get("media") or {}
    check("the PDF is a document", document.get("kind"), "document")
    check("the page count survives", document.get("pages"), 1)
    pdf_response = client.get(document.get("path"))
    check("the PDF is served", pdf_response.status_code, 200)
    check("the PDF downloads", "attachment" in
          (pdf_response.headers.get("Content-Disposition") or "").lower(), True)
    pdf_response.close()

    # 4. Video: signature detection and a page/email split.
    made = _upload(client, _video(), "clip.mp4", "A short clip", "A product demo")
    check("a video upload is accepted", made.status_code, 201)
    video = (made.get_json() or {}).get("media") or {}
    check("the video is a video", video.get("kind"), "video")
    check("the video uses the page tag", "<video" in video.get("site_html", ""), True)
    check("the video uses a link in email", "<a " in video.get("email_html", ""), True)
    check("video alt text is preserved",
          'aria-label="A short clip"' in video.get("site_html", ""), True)

    # 5. Scope: another client cannot see or delete it.
    listed = client.get("/api/media").get_json()["media"]
    check("the client sees its media", any(row["id"] == image["id"] for row in listed), True)
    search = client.get("/api/media?q=red").get_json()["media"]
    check("search finds the image", any(row["id"] == image["id"] for row in search), True)
    docs = client.get("/api/media?kind=document").get_json()["media"]
    check("the PDF filter finds the PDF", any(row["id"] == document["id"] for row in docs), True)

    _pick(client, other["id"])
    listed = client.get("/api/media").get_json()["media"]
    check("the owner's acting-client list is scoped",
          any(row["id"] == image["id"] for row in listed), False)

    other_email = "media-other-%s@example.test" % TOKEN
    orgs_service.add_org_user(other["id"], {
        "email": other_email, "password": "secret123",
        "name": "Other Admin", "role": "client_admin"})
    other_client = appmod.app.test_client()
    login = other_client.post("/api/auth/login",
                              json={"email": other_email, "password": "secret123"})
    check("the other client admin can sign in", login.status_code, 200)
    check("another client cannot read it",
          other_client.get("/api/media/%d" % image["id"]).status_code, 404)
    check("another client cannot delete it",
          other_client.delete("/api/media/%d" % image["id"]).status_code, 404)
    check("another client's list is empty",
          other_client.get("/api/media").get_json()["media"], [])
    _pick(client, org["id"])


    # 6. Editing keeps the rule, and the list_media tool carries the alt text.
    updated = client.put("/api/media/%d" % image["id"],
                         json={"alt_text": "A brighter red rectangle",
                               "description": "Updated description"})
    check("alt text can be edited", updated.status_code, 200)
    check("the edit is saved",
          updated.get_json()["media"]["alt_text"], "A brighter red rectangle")
    blank = client.put("/api/media/%d" % image["id"],
                       json={"alt_text": " ", "description": "still here"})
    check("alt text cannot be blanked", blank.status_code, 400)

    job_id = jobs.create_job("media check", "use the image", None, "selector", "chat")
    with db.get_connection() as conn:
        conn.execute("UPDATE jobs SET org_id = ? WHERE id = ?", (org["id"], job_id))
    tool = tool_registry.resolve(["list_media"], job_id=job_id)[0]
    answer = asyncio.run(tool.run_json({"query": "red"}, CancellationToken()))
    check("the team tool lists the image",
          "A brighter red rectangle" in answer, True)
    check("the team tool returns the public path",
          "/media/%d/" % image["id"] in answer, True)
    check("the team tool returns HTML with alt",
          'alt="A brighter red rectangle"' in answer, True)

    # 7. Oversize and wrong type are refused without a row.
    before = _count(org["id"])
    big = _upload(client, b"x" * 20000, "big.png", "Big", "Too large")
    check("oversize is refused", big.status_code in (400, 413), True)
    bad = _upload(client, b"<html>not an image</html>", "evil.png", "Bad", "Bad")
    check("a non-image is refused", bad.status_code, 400)
    check("refused files leave no rows", _count(org["id"]), before)

    # 8. Delete removes the row and both files.
    files = (media_library.file_path(image), media_library.thumb_path(image))
    removed = client.delete("/api/media/%d" % image["id"])
    check("delete succeeds", removed.status_code, 200)
    check("the image file is gone", os.path.exists(files[0]), False)
    check("the thumbnail is gone", os.path.exists(files[1]), False)
    check("the public URL is gone", client.get(image["path"]).status_code, 404)
    for item in (document, video):
        client.delete("/api/media/%d" % item["id"])

    check("all test rows are cleaned up", _count(org["id"]), 0)

    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nMEDIA OK - alt text and description are mandatory, and the files land in the library")



if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(MEDIA_DIR, ignore_errors=True)
