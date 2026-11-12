"""A read-only check of a live page, for the Manager and a review team.

The QA job in the incident had no way to load a URL, so it wrote a report from the task
text. This module fetches the page, parses what it can without a browser, and reports
what it actually saw - including the fact that layout at a width was *not* rendered.

It is deliberately stdlib-only. The URL is restricted to the client's own site domains
and every redirect host is checked, so a prompt cannot turn it into an internal scanner.
"""

import html
import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser

USER_AGENT = "AscendPlatform-QA/1.0 (+read-only page check)"
TIMEOUT = 15
MAX_BYTES = 1_500_000
MAX_CHECKS = 24


class WebCheckError(Exception):
    """The URL is not allowed or could not be read."""


def _is_private_ip(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return (address.is_private or address.is_loopback or address.is_link_local
            or address.is_reserved or address.is_multicast or address.is_unspecified)


def _ensure_public_host(host):
    """Refuse a host that resolves to this machine or a private network."""
    if not host:
        raise WebCheckError("The URL has no host.")
    if _is_private_ip(host):
        raise WebCheckError("That host is private and cannot be checked.")
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise WebCheckError("Could not resolve %s: %s" % (host, exc))
    for info in infos:
        if _is_private_ip(info[4][0]):
            raise WebCheckError("That host resolves to a private address and cannot be checked.")


def _site_domains(org_id):
    """The domains (and aliases) this client is allowed to ask about."""
    import sites as sites_service

    if org_id is None:
        return set()
    allowed = set()
    for row in sites_service.list_sites(org_id, limit=100):
        for value in [row.get("domain")] + list(row.get("aliases") or []):
            host = str(value or "").strip().lower().rstrip(".")
            if not host:
                continue
            allowed.add(host)
            if host.startswith("www."):
                allowed.add(host[4:])
            else:
                allowed.add("www." + host)
    return allowed


def _validate_url(url, org_id, allow_private=False):
    value = str(url or "").strip()
    if not value:
        raise WebCheckError("A URL is required.")
    if "://" not in value:
        value = "https://" + value
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in ("http", "https"):
        raise WebCheckError("Only http and https pages can be checked.")
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise WebCheckError("The URL has no host.")
    if parsed.username or parsed.password:
        raise WebCheckError("A URL with credentials is not checked.")
    if not allow_private:
        allowed = _site_domains(org_id)
        if host not in allowed and not any(host.endswith("." + item) for item in allowed):
            raise WebCheckError(
                "I can only check a page on this client's sites (%s)."
                % (", ".join(sorted(allowed)[:8]) or "none configured"))
        _ensure_public_host(host)
    return parsed.geturl()


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to another public, allowed host."""

    def __init__(self, allowed, allow_private=False):
        super().__init__()
        self.allowed = allowed
        self.allow_private = allow_private

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in ("http", "https") or not host:
            raise WebCheckError("The page redirected to an unsafe URL.")
        if not self.allow_private:
            if self.allowed is not None and host not in self.allowed and not any(
                    host.endswith("." + item) for item in self.allowed):
                raise WebCheckError("The page redirected to a host outside this client's sites.")
            _ensure_public_host(host)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url, allowed, method="GET", allow_private=False):
    request = urllib.request.Request(url, method=method, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    })
    opener = urllib.request.build_opener(_SafeRedirect(allowed, allow_private))
    try:
        response = opener.open(request, timeout=TIMEOUT)
    except urllib.error.HTTPError as exc:
        return exc.geturl() or url, exc.code, dict(exc.headers or {}), b""
    except Exception as exc:
        raise WebCheckError("Could not load %s: %s" % (url, exc))
    status = getattr(response, "status", 200)
    headers = dict(response.headers or {})
    body = b"" if method == "HEAD" else response.read(MAX_BYTES + 1)
    final = response.geturl() or url
    return final, status, headers, body[:MAX_BYTES]


class _PageParser(HTMLParser):
    """The parts of a page a review can check without a browser."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = []
        self.meta = {}
        self.headings = {"h1": [], "h2": []}
        self.links = []
        self.images = []
        self.styles = []
        self.text = []
        self._tag = None
        self._buffer = []

    def handle_starttag(self, tag, attrs):
        tag = (tag or "").lower()
        values = {key.lower(): (value or "") for key, value in attrs}
        if tag == "title":
            self._tag, self._buffer = "title", []
        elif tag == "meta":
            name = (values.get("name") or values.get("property") or "").lower()
            if name:
                self.meta[name] = values.get("content") or ""
        elif tag in ("h1", "h2"):
            self._tag, self._buffer = tag, []
        elif tag == "a":
            self.links.append(values.get("href") or "")
        elif tag == "img":
            self.images.append({
                "src": values.get("src") or "",
                "alt": values.get("alt") or "",
                "width": values.get("width") or "",
                "height": values.get("height") or "",
            })
        elif tag in ("style", "script"):
            self._tag, self._buffer = tag, []

    def handle_data(self, data):
        if self._tag:
            self._buffer.append(data)
        else:
            self.text.append(data)

    def handle_endtag(self, tag):
        tag = (tag or "").lower()
        if tag == self._tag:
            value = " ".join("".join(self._buffer).split())
            if tag == "title":
                self.title.append(value)
            elif tag in ("h1", "h2"):
                self.headings[tag].append(value)
            elif tag == "style":
                self.styles.append("".join(self._buffer))
            self._tag, self._buffer = None, []


def _header(headers, name):
    for key, value in (headers or {}).items():
        if key.lower() == name.lower():
            return value
    return ""


def _absolute(base, value):
    href = html.unescape(str(value or "").strip())
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return ""
    return urllib.parse.urljoin(base, href)


def _check_target(url, method="HEAD", allow_private=False):
    """A link or image check. Any public host is allowed; private ones are refused."""
    try:
        final, status, headers, body = _open(url, None, method=method,
                                              allow_private=allow_private)
        if method == "HEAD" and status >= 400:
            final, status, headers, body = _open(url, None, method="GET",
                                                  allow_private=allow_private)
        return {
            "url": final,
            "status": status,
            "ok": 200 <= status < 400,
            "content_type": _header(headers, "content-type"),
            "error": "" if 200 <= status < 400 else "HTTP %d" % status,
        }
    except WebCheckError as exc:
        return {"url": url, "status": 0, "ok": False, "content_type": "",
                "error": str(exc)}


def inspect(url, org_id, allow_private=False, check_links=True):
    """Fetch one page and report what can be checked without rendering it."""
    value = _validate_url(url, org_id, allow_private=allow_private)
    allowed = None if allow_private else _site_domains(org_id)
    final, status, headers, body = _open(value, allowed, allow_private=allow_private)
    content_type = _header(headers, "content-type")
    text = body.decode("utf-8", "replace")
    parser = _PageParser()
    try:
        parser.feed(text)
    except Exception:
        pass
    page_text = " ".join(" ".join(parser.text).split())
    links = []
    seen = set()
    for href in parser.links:
        absolute = _absolute(final, href)
        if absolute and absolute not in seen:
            seen.add(absolute)
            links.append({"href": href, "url": absolute})
    images = []
    image_seen = set()
    for image in parser.images:
        absolute = _absolute(final, image.get("src"))
        if absolute and absolute not in image_seen:
            image_seen.add(absolute)
            images.append({**image, "url": absolute})
    if check_links:
        with ThreadPoolExecutor(max_workers=6) as pool:
            for item, result in zip(links[:MAX_CHECKS],
                                    pool.map(lambda entry: _check_target(
                                        entry["url"], allow_private=allow_private),
                                             links[:MAX_CHECKS])):
                item.update({key: result[key] for key in
                             ("status", "ok", "content_type", "error")})
            for item, result in zip(images[:MAX_CHECKS],
                                    pool.map(lambda entry: _check_target(
                                        entry["url"], allow_private=allow_private),
                                             images[:MAX_CHECKS])):
                item.update({key: result[key] for key in
                             ("status", "ok", "content_type", "error")})
    placeholders = []
    for match in re.finditer(
            r"\[[^\]]{0,80}(?:insert|your|todo|placeholder)[^\]]{0,40}\]"
            r"|\b(?:lorem ipsum|todo|tbd)\b", page_text, re.I):
        found = match.group(0).strip()
        if found and found not in placeholders:
            placeholders.append(found)
    media_queries = []
    for style in parser.styles:
        for match in re.finditer(r"@media[^{]+", style, re.I):
            item = " ".join(match.group(0).split())
            if item not in media_queries:
                media_queries.append(item)
    return {
        "url": final,
        "status": status,
        "content_type": content_type,
        "bytes": len(body),
        "title": (parser.title[0] if parser.title else ""),
        "meta_description": parser.meta.get("description", ""),
        "meta_viewport": parser.meta.get("viewport", ""),
        "h1": parser.headings["h1"],
        "h2": parser.headings["h2"],
        "links": links[:MAX_CHECKS],
        "broken_links": [item for item in links[:MAX_CHECKS]
                         if item.get("status") and not item.get("ok")],
        "unverified_links": [item for item in links[:MAX_CHECKS]
                             if not item.get("status")],
        "images": images[:MAX_CHECKS],
        "broken_images": [item for item in images[:MAX_CHECKS]
                          if item.get("status") and not item.get("ok")],
        "unverified_images": [item for item in images[:MAX_CHECKS]
                              if not item.get("status")],
        "media_queries": media_queries,
        "placeholders": placeholders[:12],
        "starter_page": "This site is ready" in page_text,
        "rendered": False,
        "note": ("HTML was fetched and parsed. Layout at desktop or mobile widths was "
                 "not rendered; use a browser for the visual check."),
    }
