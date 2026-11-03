"""Generated image URLs are public; the name is the only thing protecting them.

The artifact route has no session - a published page embeds <img src> - and job ids are
sequential. A default name like image.png would make another client image one guess away,
so both writers prefix a random token. These are the names they produce.
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.pop("ALLOW_LIVE_ACTIONS", None)

from tools import media, pexels  # noqa: E402

FAILED = []
TOKEN = re.compile(r"^[0-9a-f]{16}-")


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name +
          ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


def test_generated_images():
    one = media._safe_name("image.png")
    two = media._safe_name("image.png")
    check("the extension is kept", one.endswith("-image.png"), True)
    check("a 16-hex token prefixes it", bool(TOKEN.match(one)), True)
    check("two images never share a name", one != two, True)
    check("the default name alone is not the path", one != "image.png", True)
    check("traversal is stripped", ".." not in media._safe_name("../../x.png"), True)


def test_stock_photos():
    one = pexels._destination(4242, "photo.jpg")
    two = pexels._destination(4242, "photo.jpg")
    check("still one folder per job",
          os.path.basename(os.path.dirname(one)), "job-4242")
    check("the token is in the file name",
          bool(TOKEN.match(os.path.basename(one))), True)
    check("the requested name is kept", one.endswith("-photo.jpg"), True)
    check("two downloads never share a name", one != two, True)
    check("an unknown suffix is normalised",
          pexels._destination(1, "x.exe").endswith(".jpg"), True)
    check("traversal is stripped",
          ".." not in pexels._destination(1, "../../evil.jpg"), True)


def main():
    test_generated_images()
    test_stock_photos()
    if FAILED:
        print("\n%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
        raise SystemExit(1)
    print("\nARTIFACT PATHS OK - public image URLs carry a random token")


if __name__ == "__main__":
    main()
