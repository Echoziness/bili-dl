"""Shared HTTP transport for Bilibili authentication and content APIs.

Web-session and content modules use this boundary for browser headers, proxy
selection, timeouts, decoding, and structured failures.  ``proxy=None``
honours urllib's environment defaults; ``proxy=""`` explicitly disables
environment proxies; any non-empty value is used for both HTTP and HTTPS.
"""

from __future__ import annotations

import gzip
import http.cookiejar
import json
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .config import AUTH_API_TIMEOUT, REFERER, USER_AGENT


@dataclass(frozen=True)
class HttpFailure:
    """A display-safe transport failure without response bodies or secrets."""

    kind: str
    status: Optional[int] = None

    def describe(self) -> str:
        if self.kind == "network":
            return "网络连接失败"
        if self.kind == "http" and self.status is not None:
            return f"HTTP {self.status}"
        if self.kind == "bad_json":
            return "B 站返回非 JSON 内容"
        return "B 站返回了异常数据"


def request(
    url: str, data: Optional[bytes] = None, headers: Optional[dict[str, str]] = None
) -> urllib.request.Request:
    """Build one Bilibili request with consistent browser-facing headers."""
    merged = {"Referer": REFERER, "User-Agent": USER_AGENT}
    if headers:
        merged.update(headers)
    if data is not None:
        merged["Content-Type"] = "application/x-www-form-urlencoded"
    return urllib.request.Request(url, data=data, headers=merged)


def cookie_opener(
    jar: Optional[http.cookiejar.CookieJar] = None, proxy: Optional[str] = None
) -> urllib.request.OpenerDirector:
    """Build an opener with explicit Cookie and proxy semantics."""
    handlers: list[urllib.request.BaseHandler] = []
    if proxy is not None:
        proxies = {"http": proxy, "https": proxy} if proxy else {}
        handlers.append(urllib.request.ProxyHandler(proxies))
    if jar is not None:
        handlers.append(urllib.request.HTTPCookieProcessor(jar))
    return urllib.request.build_opener(*handlers)


def load_cookie_jar(
    cookie_path: Path,
) -> tuple[Optional[http.cookiejar.MozillaCookieJar], Optional[str]]:
    """Load the existing session for content requests and session renewal."""
    jar = http.cookiejar.MozillaCookieJar(str(cookie_path))
    try:
        jar.load(ignore_discard=True, ignore_expires=True)
    except (OSError, http.cookiejar.LoadError):
        return None, "无法读取现有 Cookie"
    return jar, None


def resolve_url(
    opener: urllib.request.OpenerDirector, url: str, timeout: float
) -> tuple[Optional[str], Optional[HttpFailure]]:
    """Resolve a share-link redirect without downloading the response body."""
    try:
        with opener.open(request(url), timeout=timeout) as response:
            return str(response.geturl()), None
    except urllib.error.HTTPError as exc:
        return None, HttpFailure("http", exc.code)
    except (urllib.error.URLError, OSError):
        return None, HttpFailure("network")


def _decode_body(
    body: bytes, content_encoding: Optional[str]
) -> tuple[Optional[str], Optional[HttpFailure]]:
    """Decode one HTTP body without exposing compressed or malformed content."""
    encoding = (content_encoding or "").strip().lower()
    if encoding in {"gzip", "x-gzip"}:
        try:
            body = gzip.decompress(body)
        except (gzip.BadGzipFile, EOFError, zlib.error):
            return None, HttpFailure("bad_data")
    elif encoding not in {"", "identity"}:
        return None, HttpFailure("bad_data")
    return body.decode("utf-8", errors="replace"), None


def fetch_json(
    opener: urllib.request.OpenerDirector,
    req: urllib.request.Request,
    timeout: float = AUTH_API_TIMEOUT,
) -> tuple[Optional[dict[str, Any]], Optional[HttpFailure]]:
    """Fetch and decode a JSON object using the shared failure vocabulary."""
    try:
        with opener.open(req, timeout=timeout) as response:
            raw = response.read()
            content_encoding = response.headers.get("Content-Encoding")
    except urllib.error.HTTPError as exc:
        return None, HttpFailure("http", exc.code)
    except (urllib.error.URLError, OSError):
        return None, HttpFailure("network")
    body, failure = _decode_body(raw, content_encoding)
    if body is None:
        return None, failure
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None, HttpFailure("bad_json")
    if not isinstance(payload, dict):
        return None, HttpFailure("bad_data")
    return payload, None


def fetch_text(
    opener: urllib.request.OpenerDirector,
    req: urllib.request.Request,
    timeout: float = AUTH_API_TIMEOUT,
) -> tuple[Optional[str], Optional[HttpFailure]]:
    """Fetch UTF-8 text using the shared failure vocabulary."""
    try:
        with opener.open(req, timeout=timeout) as response:
            raw = response.read()
            content_encoding = response.headers.get("Content-Encoding")
    except urllib.error.HTTPError as exc:
        return None, HttpFailure("http", exc.code)
    except (urllib.error.URLError, OSError):
        return None, HttpFailure("network")
    return _decode_body(raw, content_encoding)
