"""Bilibili's documented Web Cookie-refresh protocol.

This module never touches terminal output or disk.  It uses a CookieJar as the
protocol source of truth, returns a candidate new session to the store layer,
and lets that layer validate and persist it before the old refresh token is
confirmed as spent.
"""

from __future__ import annotations

import http.cookiejar
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional

from .config import (
    COOKIE_CONFIRM_REFRESH_API,
    COOKIE_INFO_API,
    COOKIE_REFRESH_API,
    CORRESPOND_URL_PREFIX,
    NAV_TIMEOUT,
    REFERER,
    USER_AGENT,
)

_PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDLgd2OAkcGVtoE3ThUREbio0Eg
Uc/prcajMKXvkCKFCWhJYJcLkcM2DKKcSeFpD/j6Boy538YXnR6VhcuUJOhH2x71
nzPjfdTcqMz7djHum0qSZA0AyCBDABUqCrfNgCiJ00Ra7GmRj+YCK1NJEuewlb40
JNrRuoEUXpabUzGB8QIDAQAB
-----END PUBLIC KEY-----
"""


@dataclass
class RenewalResult:
    """Outcome of checking or refreshing a Web session, with no disk effects."""

    checked: bool = False
    refreshed: bool = False
    cookie_lines: list[str] = field(default_factory=list)
    refresh_token: Optional[str] = None
    old_refresh_token: Optional[str] = None
    opener: Optional[urllib.request.OpenerDirector] = None
    jar: Optional[http.cookiejar.CookieJar] = None
    messages: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class RenewalRequirement:
    """Read-only answer to whether Bilibili currently requests renewal."""

    required: Optional[bool] = None
    timestamp: Optional[int] = None
    opener: Optional[urllib.request.OpenerDirector] = None
    jar: Optional[http.cookiejar.CookieJar] = None
    error: Optional[str] = None


class _RefreshCsrfParser(HTMLParser):
    """Extract the server-rendered token from Bilibili's correspond page."""

    def __init__(self) -> None:
        super().__init__()
        self._in_target = False
        self.value: Optional[str] = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        self._in_target = tag == "div" and dict(attrs).get("id") == "1-name"

    def handle_endtag(self, tag: str) -> None:
        if tag == "div":
            self._in_target = False

    def handle_data(self, data: str) -> None:
        if self._in_target and self.value is None:
            value = data.strip()
            if value:
                self.value = value


def crypto_available() -> bool:
    """Whether the bundled, audited RSA implementation is importable."""
    try:
        import cryptography  # noqa: F401
    except ImportError:
        return False
    return True


def _request(url: str, data: Optional[bytes] = None) -> urllib.request.Request:
    headers = {"Referer": REFERER, "User-Agent": USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    return urllib.request.Request(url, data=data, headers=headers)


def _request_json(
    opener: urllib.request.OpenerDirector, request: urllib.request.Request
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    try:
        with opener.open(request, timeout=NAV_TIMEOUT) as response:
            body = response.read().decode("utf-8", errors="replace")
        payload = json.loads(body)
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError):
        return None, "网络连接失败"
    except json.JSONDecodeError:
        return None, "B 站返回非 JSON 内容"
    if not isinstance(payload, dict):
        return None, "B 站返回了异常数据"
    return payload, None


def _request_text(
    opener: urllib.request.OpenerDirector, request: urllib.request.Request
) -> tuple[Optional[str], Optional[str]]:
    try:
        with opener.open(request, timeout=NAV_TIMEOUT) as response:
            return response.read().decode("utf-8", errors="replace"), None
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError):
        return None, "网络连接失败"


def _load_jar(cookie_path: Path) -> tuple[Optional[http.cookiejar.MozillaCookieJar], Optional[str]]:
    jar = http.cookiejar.MozillaCookieJar(str(cookie_path))
    try:
        jar.load(ignore_discard=True, ignore_expires=True)
    except (OSError, http.cookiejar.LoadError):
        return None, "无法读取现有 Cookie"
    return jar, None


def _cookie_value(jar: http.cookiejar.CookieJar, name: str) -> Optional[str]:
    for cookie in jar:
        domain = cookie.domain.lstrip(".").lower()
        if (domain == "bilibili.com" or domain.endswith(".bilibili.com")) and cookie.name == name:
            return cookie.value
    return None


def _netscape_lines(jar: http.cookiejar.CookieJar) -> list[str]:
    """Serialize the Bilibili subset only, preserving the privacy boundary."""
    lines = ["# Netscape HTTP Cookie File"]
    for cookie in jar:
        domain = cookie.domain.lstrip(".").lower()
        if domain != "bilibili.com" and not domain.endswith(".bilibili.com"):
            continue
        domain_match = "TRUE" if cookie.domain.startswith(".") else "FALSE"
        secure = "TRUE" if cookie.secure else "FALSE"
        lines.append(
            "\t".join(
                (
                    cookie.domain,
                    domain_match,
                    cookie.path or "/",
                    secure,
                    str(cookie.expires or 0),
                    cookie.name,
                    cookie.value or "",
                )
            )
        )
    return lines


def _correspond_path(timestamp: int) -> tuple[Optional[str], Optional[str]]:
    """RSA-OAEP encrypt ``refresh_<timestamp>`` using Bilibili's public key."""
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
    except ImportError:
        return None, "未安装会话续期组件（请重新安装: pip install -U bili-dl）"
    try:
        public_key = load_pem_public_key(_PUBLIC_KEY_PEM)
        if not isinstance(public_key, RSAPublicKey):
            return None, "会话续期公钥类型无效"
        encrypted = public_key.encrypt(
            f"refresh_{timestamp}".encode(),
            padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
        )
    except (TypeError, ValueError):
        return None, "无法生成会话续期口令"
    return encrypted.hex(), None


def _refresh_csrf(
    opener: urllib.request.OpenerDirector, timestamp: int
) -> tuple[Optional[str], Optional[str]]:
    correspond_path, error = _correspond_path(timestamp)
    if correspond_path is None:
        return None, error
    page, error = _request_text(opener, _request(f"{CORRESPOND_URL_PREFIX}{correspond_path}"))
    if page is None:
        return None, error
    parser = _RefreshCsrfParser()
    parser.feed(page)
    if not parser.value:
        return None, "B 站未返回会话续期口令"
    return parser.value, None


def _renewal_requirement(cookie_path: Path) -> RenewalRequirement:
    """Ask Bilibili whether it currently requests a Web-session refresh."""
    jar, error = _load_jar(cookie_path)
    if jar is None:
        return RenewalRequirement(error=error)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    csrf = _cookie_value(jar, "bili_jct")
    if not csrf:
        return RenewalRequirement(error="Cookie 缺少 bili_jct")
    info_url = f"{COOKIE_INFO_API}?{urllib.parse.urlencode({'csrf': csrf})}"
    payload, error = _request_json(opener, _request(info_url))
    if payload is None:
        return RenewalRequirement(error=error)
    data = payload.get("data")
    if payload.get("code") != 0 or not isinstance(data, dict):
        return RenewalRequirement(error=str(payload.get("message") or "B 站拒绝检查会话续期"))
    required = data.get("refresh")
    if not isinstance(required, bool):
        return RenewalRequirement(error="B 站未返回会话续期状态")
    timestamp = data.get("timestamp")
    if required and not isinstance(timestamp, int):
        return RenewalRequirement(error="B 站未返回会话续期时间戳")
    return RenewalRequirement(
        required=required,
        timestamp=timestamp if isinstance(timestamp, int) else None,
        opener=opener,
        jar=jar,
    )


def check_requirement(cookie_path: Path) -> tuple[Optional[bool], Optional[str]]:
    """Read Bilibili's current refresh requirement without changing a session."""
    result = _renewal_requirement(cookie_path)
    return result.required, result.error


def check_and_refresh(cookie_path: Path, refresh_token: str) -> RenewalResult:
    """Check Bilibili's daily renewal flag and refresh when it requests one.

    The returned candidate is not written to disk and old refresh-token
    confirmation is intentionally deferred to :func:`confirm`.
    """
    requirement = _renewal_requirement(cookie_path)
    if requirement.error:
        return RenewalResult(messages=[("warn", f"[登录] 无法检查会话续期：{requirement.error}")])
    if requirement.required is False:
        return RenewalResult(checked=True)
    if requirement.timestamp is None or requirement.opener is None or requirement.jar is None:
        return RenewalResult(messages=[("warn", "[登录] 会话续期检查缺少上下文")])
    csrf = _cookie_value(requirement.jar, "bili_jct")
    if not csrf:
        return RenewalResult(messages=[("warn", "[登录] 刷新前会话缺少 bili_jct")])

    refresh_csrf, error = _refresh_csrf(requirement.opener, requirement.timestamp)
    if refresh_csrf is None:
        return RenewalResult(messages=[("warn", f"[登录] 无法刷新会话：{error}")])
    form = urllib.parse.urlencode(
        {
            "csrf": csrf,
            "refresh_csrf": refresh_csrf,
            "source": "main_web",
            "refresh_token": refresh_token,
        }
    ).encode()
    payload, error = _request_json(requirement.opener, _request(COOKIE_REFRESH_API, form))
    if payload is None:
        return RenewalResult(messages=[("warn", f"[登录] 无法刷新会话：{error}")])
    data = payload.get("data")
    new_token = data.get("refresh_token") if isinstance(data, dict) else None
    if payload.get("code") != 0 or not isinstance(new_token, str) or not new_token:
        message = str(payload.get("message") or "B 站拒绝刷新会话")
        return RenewalResult(messages=[("warn", f"[登录] 无法刷新会话：{message}")])

    lines = _netscape_lines(requirement.jar)
    if not _cookie_value(requirement.jar, "SESSDATA"):
        return RenewalResult(messages=[("warn", "[登录] 刷新未返回 SESSDATA，保留原会话")])
    return RenewalResult(
        checked=True,
        refreshed=True,
        cookie_lines=lines,
        refresh_token=new_token,
        old_refresh_token=refresh_token,
        opener=requirement.opener,
        jar=requirement.jar,
    )


def _confirm(
    opener: urllib.request.OpenerDirector,
    jar: http.cookiejar.CookieJar,
    old_refresh_token: str,
) -> Optional[str]:
    csrf = _cookie_value(jar, "bili_jct")
    if not csrf:
        return "刷新后的会话缺少 bili_jct"
    form = urllib.parse.urlencode({"csrf": csrf, "refresh_token": old_refresh_token}).encode()
    payload, error = _request_json(opener, _request(COOKIE_CONFIRM_REFRESH_API, form))
    if payload is None:
        return error
    if payload.get("code") != 0:
        return str(payload.get("message") or "B 站拒绝确认会话续期")
    return None


def confirm(result: RenewalResult) -> Optional[str]:
    """Confirm a durably saved refresh, retiring the old refresh credential."""
    if (
        not result.refreshed
        or result.opener is None
        or result.jar is None
        or result.old_refresh_token is None
    ):
        return "会话续期确认缺少上下文"
    return _confirm(result.opener, result.jar, result.old_refresh_token)


def confirm_pending(cookie_path: Path, old_refresh_token: str) -> Optional[str]:
    """Retry a previously persisted old-token confirmation."""
    jar, error = _load_jar(cookie_path)
    if jar is None:
        return error
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    return _confirm(opener, jar, old_refresh_token)
