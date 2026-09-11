"""Shared HTTP transport for Bilibili authentication and session APIs.

All Web-session modules use this boundary for browser headers, proxy
selection, timeouts, decoding, and structured failures.  ``proxy=None``
honours urllib's environment defaults; ``proxy=""`` explicitly disables
environment proxies; any non-empty value is used for both HTTP and HTTPS.
"""

from __future__ import annotations

import http.cookiejar
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
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


def fetch_json(
    opener: urllib.request.OpenerDirector,
    req: urllib.request.Request,
    timeout: float = AUTH_API_TIMEOUT,
) -> tuple[Optional[dict[str, Any]], Optional[HttpFailure]]:
    """Fetch and decode a JSON object using the shared failure vocabulary."""
    try:
        with opener.open(req, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
        payload = json.loads(body)
    except urllib.error.HTTPError as exc:
        return None, HttpFailure("http", exc.code)
    except (urllib.error.URLError, OSError):
        return None, HttpFailure("network")
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
            return response.read().decode("utf-8", errors="replace"), None
    except urllib.error.HTTPError as exc:
        return None, HttpFailure("http", exc.code)
    except (urllib.error.URLError, OSError):
        return None, HttpFailure("network")
