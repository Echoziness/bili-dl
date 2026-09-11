"""Minimal Bilibili Web QR login protocol.

This module creates a standalone Bilibili Web session without reading a
browser profile.  Its caller can persist the Web ``refresh_token`` returned
with a successful QR login for the separately implemented renewal flow.

The module has no terminal side effects.  It returns structured results and a
rendered QR string for :mod:`bili_dl.cli` to present.
"""

from __future__ import annotations

import http.cookiejar
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

from . import transport
from .config import (
    QR_GENERATE_API,
    QR_LOGIN_MAX_POLLS,
    QR_POLL_API,
    QR_POLL_INTERVAL,
    QR_TIMEOUT,
)


@dataclass
class QrSession:
    """An in-memory QR key and its Bilibili-only cookie jar."""

    url: str
    key: str
    jar: http.cookiejar.CookieJar
    opener: urllib.request.OpenerDirector


@dataclass
class QrStartResult:
    """Outcome of asking Bilibili to issue a QR-login key."""

    session: Optional[QrSession] = None
    messages: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class QrPollResult:
    """Outcome of waiting for the user to confirm a QR login."""

    success: bool
    cookie_lines: list[str] = field(default_factory=list)
    refresh_token: Optional[str] = None
    messages: list[tuple[str, str]] = field(default_factory=list)


def qrcode_available() -> bool:
    """Whether the bundled QR renderer is importable in this installation."""
    try:
        import qrcode  # noqa: F401
    except ImportError:
        return False
    return True


def render_terminal_qr(url: str) -> str:
    """Return a Unicode block QR image for *url*.

    ``qrcode`` is a standard runtime dependency. Call
    :func:`qrcode_available` first so a damaged installation gets a useful
    recovery message instead of an import traceback.
    """
    import qrcode

    code = qrcode.QRCode(border=1)
    code.add_data(url)
    code.make(fit=True)
    matrix = code.get_matrix()
    return "\n".join("".join("██" if cell else "  " for cell in row) for row in matrix)


def start(*, proxy: Optional[str] = None) -> QrStartResult:
    """Request a new Bilibili Web QR login session."""
    jar = http.cookiejar.CookieJar()
    opener = transport.cookie_opener(jar, proxy)
    data, failure = transport.fetch_json(
        opener, transport.request(QR_GENERATE_API), timeout=QR_TIMEOUT
    )
    if data is None:
        detail = failure.describe() if failure else "未知错误"
        return QrStartResult(messages=[("error", f"[登录] 获取二维码失败：{detail}")])

    payload = data.get("data")
    if data.get("code") != 0 or not isinstance(payload, dict):
        message = str(data.get("message") or "B 站拒绝生成二维码")
        return QrStartResult(messages=[("error", f"[登录] 获取二维码失败：{message}")])

    url = payload.get("url")
    key = payload.get("qrcode_key")
    if not isinstance(url, str) or not url or not isinstance(key, str) or not key:
        return QrStartResult(messages=[("error", "[登录] B 站未返回可用的二维码")])
    return QrStartResult(session=QrSession(url=url, key=key, jar=jar, opener=opener))


def _is_bili_domain(domain: str) -> bool:
    normalized = domain.lstrip(".").lower()
    return normalized == "bilibili.com" or normalized.endswith(".bilibili.com")


def _netscape_lines(jar: http.cookiejar.CookieJar) -> list[str]:
    """Serialize only Bilibili cookies from a CookieJar in Netscape format."""
    lines = ["# Netscape HTTP Cookie File"]
    for cookie in jar:
        if not _is_bili_domain(cookie.domain):
            continue
        domain_match = "TRUE" if cookie.domain.startswith(".") else "FALSE"
        secure = "TRUE" if cookie.secure else "FALSE"
        expires = str(cookie.expires or 0)
        lines.append(
            "\t".join(
                (
                    cookie.domain,
                    domain_match,
                    cookie.path or "/",
                    secure,
                    expires,
                    cookie.name,
                    cookie.value or "",
                )
            )
        )
    return lines


def _fallback_cookie_lines(payload: dict[str, Any]) -> list[str]:
    """Read Web-login cookies from the success URL if headers omit them.

    Current Bilibili responses normally use ``Set-Cookie`` headers.  Older
    response variants placed the same fields in ``data.url`` instead, so this
    fallback makes the experiment robust without contacting another service.
    """
    value = payload.get("url")
    if not isinstance(value, str) or not value:
        return []
    fields = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(value).query))
    allowed = ("DedeUserID", "DedeUserID__ckMd5", "SESSDATA", "bili_jct", "sid")
    lines = ["# Netscape HTTP Cookie File"]
    for name in allowed:
        if cookie := fields.get(name):
            lines.append(f".bilibili.com\tTRUE\t/\tTRUE\t0\t{name}\t{cookie}")
    return lines


def _has_sessdata(lines: list[str]) -> bool:
    return any(
        len(fields := line.split("\t")) >= 7
        and _is_bili_domain(fields[0])
        and fields[5] == "SESSDATA"
        and bool(fields[6])
        for line in lines
    )


def poll(session: QrSession, *, sleep: bool = True) -> QrPollResult:
    """Wait for a scan/confirmation and return its Bilibili cookie lines."""
    poll_url = f"{QR_POLL_API}?{urllib.parse.urlencode({'qrcode_key': session.key})}"
    for _ in range(QR_LOGIN_MAX_POLLS):
        data, failure = transport.fetch_json(
            session.opener, transport.request(poll_url), timeout=QR_TIMEOUT
        )
        if data is None:
            detail = failure.describe() if failure else "未知错误"
            return QrPollResult(False, messages=[("error", f"[登录] 查询扫码状态失败：{detail}")])

        payload = data.get("data")
        if data.get("code") != 0 or not isinstance(payload, dict):
            message = str(data.get("message") or "B 站返回异常状态")
            return QrPollResult(False, messages=[("error", f"[登录] 查询扫码状态失败：{message}")])

        status = payload.get("code")
        if status == 0:
            lines = _netscape_lines(session.jar)
            if not _has_sessdata(lines):
                lines = _fallback_cookie_lines(payload)
            if not _has_sessdata(lines):
                return QrPollResult(
                    False, messages=[("error", "[登录] 扫码成功，但未收到 SESSDATA")]
                )
            refresh_token = payload.get("refresh_token")
            if not isinstance(refresh_token, str) or not refresh_token:
                refresh_token = None
            messages = [("ok", "[登录] 已确认，正在验证新的 B 站会话")]
            return QrPollResult(
                True, cookie_lines=lines, refresh_token=refresh_token, messages=messages
            )
        if status == 86038:
            return QrPollResult(
                False, messages=[("warn", "[登录] 二维码已过期，请重新执行 bili-dl login")]
            )
        # 86101 means not scanned; 86090 means scanned but the user has not
        # confirmed it in the App yet.  Bilibili returns 86090 repeatedly
        # while the confirmation screen is open, so both are wait states.
        if status not in (86101, 86090):
            message = str(payload.get("message") or f"未知状态 {status}")
            return QrPollResult(False, messages=[("error", f"[登录] 扫码失败：{message}")])

        if sleep:
            time.sleep(QR_POLL_INTERVAL)

    return QrPollResult(False, messages=[("warn", "[登录] 等待扫码超时，请重新执行 bili-dl login")])
