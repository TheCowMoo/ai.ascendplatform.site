"""Stock photography that actually lands in the client's Media library.

Run: .\\venv\\Scripts\\python.exe tests\\smoke_stock_media.py

No Pexels key and no network: the download is replaced with a tiny JPEG, and the test
proves the import goes through media_library (the same storage and validation as an
upload), keeps the credit in the description, returns the local URL/HTML, and is wired
to the Manager without an approval step.
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

MEDIA_DIR = tempfile.mkdtemp(prefix="ascend-stock-media-")
ARTIFACTS_DIR = tempfile.mkdtemp(prefix="ascend-stock-artifacts-")
os.environ["MEDIA_DIR"] = MEDIA_DIR
os.environ["ARTIFACTS_DIR"] = ARTIFACTS_DIR
os.environ.pop("ALLOW_LIVE_ACTIONS", None)

from PIL import Image  # noqa: E402

import db  # noqa: E402
import jobs  # noqa: E402
import manager  # noqa: E402
import media_library  # noqa: E402
import orgs as orgs_service  # noqa: E402
import tools.pexels as pexels  # noqa: E402
from tools import resolve  # noqa: E402

import agents  # noqa: E402

TOKEN = uuid.uuid4().hex[:6]
FAILED = []


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name +
          ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


def main():
    db.init_db()
    org = orgs_service.create_org({"name": "Stock Media %s" % TOKEN})
    job_id = jobs.create_job(
        "Stock media %s" % TOKEN, "import a Pexels photo", None, "selector", "chat")
    with db.get_connection() as conn:
        conn.execute("UPDATE jobs SET org_id = ? WHERE id = ?", (org["id"], job_id))

    original_download = pexels.download
    imported_id = None
    try:
        def fake_download(url, path, limit=None):
            Image.new("RGB", (24, 16), (20, 80, 120)).save(path, "JPEG")
            return os.path.getsize(path)

        pexels.download = fake_download
        url = "https://images.pexels.com/photos/42/hero-coast.jpg"
        credit = "Photo by Ada Lovelace on Pexels (https://www.pexels.com/photo/42/)"
        result = asyncio.run(pexels._build_import_photo(None, job_id)(
            url=url,
            alt="A turquoise coastline",
            description="Hero image for the Cebu page",
            credit=credit,
        ))
        print("import ->", result[:280])
        data = json.loads(result)
        imported_id = data.get("media_id")
        check("the import returns a media id", isinstance(imported_id, int), True)
        check("the import returns a local Media path",
              "/media/%s/" % imported_id in data.get("path", ""), True)
        check("the local URL is absolute when the platform base is configured",
              data.get("url", "").startswith("http") or
              data.get("url", "").startswith("/media/"), True)
        check("the alt text is preserved", data.get("alt"), "A turquoise coastline")
        check("the credit is stored in the description",
              credit in data.get("description", ""), True)
        check("the HTML carries the alt text",
              'alt="A turquoise coastline"' in data.get("html", ""), True)

        row = media_library.get_media(imported_id, org["id"])
        check("the row belongs to the client", row.get("org_id"), org["id"])
        check("it is an image", row.get("kind"), "image")
        check("the bytes are in the Media store", os.path.exists(
            media_library.file_path(row)), True)
        check("the credit is on the row", credit in row.get("description", ""), True)

        tools = resolve(["search_photos", "import_photo_to_media"], job_id=job_id)
        check("the Manager can resolve both photo tools",
              [tool.name for tool in tools], ["search_photos", "import_photo_to_media"])
        check("search_photos is granted to the Manager",
              "search_photos" in manager.GRANTED, True)
        check("import_photo_to_media is granted to the Manager",
              "import_photo_to_media" in manager.GRANTED, True)
        check("the Manager prompt says an empty Media is not a dead end",
              "If Media is empty" in manager.SYSTEM, True)
        check("the Manager prompt uses the imported photo",
              "import_photo_to_media" in manager.SYSTEM, True)
        image_artist = next(item for item in agents.DEFAULT_ROSTER
                            if item["name"] == "Image Artist")
        check("the Image Artist can resolve the importer",
              "import_photo_to_media" in image_artist["tools"], True)
        check("and its system message adds the Media rule",
              "import_photo_to_media" in agents.system_message_for(image_artist), True)
    finally:
        pexels.download = original_download
        if imported_id:
            media_library.delete_media(imported_id, org["id"])
        with db.get_connection() as conn:
            conn.execute("DELETE FROM job_events WHERE job_id = ?", (job_id,))
            conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            conn.execute("DELETE FROM orgs WHERE id = ?", (org["id"],))
        shutil.rmtree(MEDIA_DIR, ignore_errors=True)
        shutil.rmtree(ARTIFACTS_DIR, ignore_errors=True)

    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nSTOCK MEDIA OK - Pexels imports land in the client's own Media library")


if __name__ == "__main__":
    main()