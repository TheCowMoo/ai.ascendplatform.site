"""The page checker a review team can actually use.

Run: .\\venv\\Scripts\\python.exe tests\\test_fetch_page.py

The QA job in the incident had no way to load a URL. This pins the replacement: a real
HTTP fetch, a title/meta/heading/link/image report, and a refusal to treat a foreign or
private host as a client page.
"""

import http.server
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import agents  # noqa: E402
import manager  # noqa: E402
import tools  # noqa: E402
import webcheck  # noqa: E402

FAILED = []
HTML = """<!doctype html><html><head>
<title>The Cow Exhibit - Come Meet the Cows</title>
<meta name="description" content="A hands-on cow exhibit.">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>@media (max-width:600px){ h1 { font-size: 24px; } }</style>
</head><body>
<h1>Come and meet the cows</h1>
<h2>Meet the herd</h2>
<a href="/missing">Broken link</a>
<a href="/pic.png">Picture</a>
<img src="/pic.png" alt="A cow in the barn" width="940" height="628">
<p>[INSERT IMAGE URL] Lorem ipsum</p>
</body></html>"""


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


class Handler(http.server.BaseHTTPRequestHandler):
    def _serve(self, body=False):
        if self.path == "/":
            payload = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if body:
                self.wfile.write(payload)
        elif self.path == "/pic.png":
            payload = b"\x89PNG\r\n\x1a\nnot-really-a-png"
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if body:
                self.wfile.write(payload)
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def do_GET(self):
        self._serve(True)

    def do_HEAD(self):
        self._serve(False)

    def log_message(self, *_args):
        pass


def test_a_real_page_is_checked():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = "http://127.0.0.1:%d/" % server.server_address[1]
        report = webcheck.inspect(url, None, allow_private=True)
    finally:
        server.shutdown()
        server.server_close()
    check("the status is read", report["status"], 200)
    check("the title is read", report["title"],
          "The Cow Exhibit - Come Meet the Cows")
    check("the meta description is read", report["meta_description"],
          "A hands-on cow exhibit.")
    check("the viewport is read", "width=device-width" in report["meta_viewport"], True)
    check("the h1 is read", report["h1"], ["Come and meet the cows"])
    check("the h2 is read", report["h2"], ["Meet the herd"])
    check("the media query is found", report["media_queries"],
          ["@media (max-width:600px)"])
    check("the broken link is flagged",
          [item["url"].rsplit("/", 1)[-1] for item in report["broken_links"]], ["missing"])
    check("the working image is not broken", report["broken_images"], [])
    check("the image alt text is carried", report["images"][0]["alt"],
          "A cow in the barn")
    check("a placeholder is found", len(report["placeholders"]) >= 2, True)
    check("layout is not claimed", report["rendered"], False)
    check("and the report says so", "not rendered" in report["note"], True)


def test_a_foreign_or_private_host_is_refused():
    try:
        webcheck.inspect("https://example.com/", None)
    except webcheck.WebCheckError as exc:
        refused = "client's sites" in str(exc)
    else:
        refused = False
    check("a foreign host is refused", refused, True)
    try:
        webcheck._ensure_public_host("127.0.0.1")
    except webcheck.WebCheckError:
        private = True
    else:
        private = False
    check("a private host is refused", private, True)


def test_the_read_tools_are_wired():
    check("fetch_page is registered", tools.unknown(["fetch_page"]), [])
    check("the Manager can fetch", "fetch_page" in manager.GRANTED, True)
    check("a read-only Manager keeps it",
          "fetch_page" in manager.granted_for({"read_only": 1}), True)
    operator = next(row for row in agents.DEFAULT_ROSTER if row["name"] == "Operator")
    check("the Operator can fetch", "fetch_page" in operator["tools"], True)
    check("and it is in the read-only allowlist",
          "fetch_page" in tools.READ_ONLY_TOOLS, True)


if __name__ == "__main__":
    try:
        test_a_real_page_is_checked()
        test_a_foreign_or_private_host_is_refused()
        test_the_read_tools_are_wired()
    finally:
        if FAILED:
            print("\n%d FAILED" % len(FAILED))
            raise SystemExit(1)
        print("\nFETCH PAGE OK - a review can inspect a live page, and nothing else")
