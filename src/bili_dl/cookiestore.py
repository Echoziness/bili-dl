"""Cookie validation and readiness orchestration.

Single responsibility: determine whether a usable Bilibili cookie file exists,
and if not, coordinate with :mod:`cookiesource` to create one.

This module is pure logic — it returns :class:`ValidationResult` /
:class:`EnsureResult` objects and never calls ``ui.*`` directly. The controller
(``cli.py``) is responsible for turning result messages into terminal output.

Online probe: the ``nav`` API requires a browser User-Agent (Bilibili returns
HTTP 412 to urllib's default ``Python-urllib/x.y`` UA). On network/SSL failure
we degrade gracefully to local-only validation.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .config import BILI_COOKIE_FILENAME, NAV_API, NAV_TIMEOUT, USER_AGENT, MsgLevel
from .cookiesource import find_source, import_cookie, read_lines
from .paths import config_dir


@dataclass
class ValidationResult:
    """Outcome of a cookie validity check."""

    valid: bool
    messages: list[tuple[MsgLevel, str]] = field(default_factory=list)
    uname: Optional[str] = None


@dataclass
class EnsureResult:
    """Outcome of the full validate → import → re-validate flow."""

    ready: bool
    messages: list[tuple[MsgLevel, str]] = field(default_factory=list)


def bili_cookie_path(cookie_dir: Optional[Path] = None) -> Path:
    return (cookie_dir or config_dir()) / BILI_COOKIE_FILENAME


def _extract_sessdata(lines: list[str]) -> Optional[str]:
    """Return the SESSDATA value from the first matching .bilibili.com line."""
    for raw_line in lines:
        line = raw_line.removeprefix("#HttpOnly_")
        if ".bilibili.com" in line and "SESSDATA" in line and not line.startswith("#"):
            fields = line.split("\t")
            if len(fields) >= 7 and fields[6]:
                return fields[6]
    return None


@dataclass
class NavProbeResult:
    """Outcome of a nav API probe.

    ``data`` holds parsed JSON on success; ``None`` on error.
    ``error`` is ``None`` on success; ``"network"`` for connection failures;
    ``"http:{status}"`` for HTTP error responses (412/403 = 风控, 5xx = 服务端).
    """

    data: Optional[dict[str, Any]] = None
    error: Optional[str] = None


def _nav_probe(sessdata: str) -> NavProbeResult:
    """Probe the nav API; return parsed JSON on success, error tag on failure.

    HTTPError is caught separately from URLError so the caller can distinguish
    "B 站风控/接口异常" (HTTP 4xx/5xx) from "本机网络不通" (URLError) —
    previously both were swallowed by ``except Exception`` and reported as
    "网络/SSL 错误", masking the real cause (AGENTS.md §2.6).
    """
    try:
        req = urllib.request.Request(
            NAV_API, headers={"Cookie": f"SESSDATA={sessdata}", "User-Agent": USER_AGENT}
        )
        with urllib.request.urlopen(req, timeout=NAV_TIMEOUT) as resp:
            data: dict[str, Any] = json.loads(resp.read().decode("utf-8", errors="replace"))
            return NavProbeResult(data=data)
    except urllib.error.HTTPError as e:
        return NavProbeResult(error=f"http:{e.code}")
    except urllib.error.URLError:
        return NavProbeResult(error="network")
    except Exception:
        return NavProbeResult(error="network")


def validate(cookie_dir: Optional[Path] = None) -> ValidationResult:
    """Check local format + online login status of the Bilibili cookie file.

    Returns a :class:`ValidationResult`. On network error, degrades to
    local-only validation (returns ``valid=True`` if format is OK).
    """
    path = bili_cookie_path(cookie_dir)
    if not path.exists():
        return ValidationResult(valid=False)

    lines = read_lines(path)
    if not lines:
        return ValidationResult(
            valid=False,
            messages=[("warn", "[提示] Cookie 文件为空")],
        )

    sessdata = _extract_sessdata(lines)
    if not sessdata:
        return ValidationResult(
            valid=False,
            messages=[("warn", "[提示] 未找到 SESSDATA（可能未登录，或 Cookie 已过期）")],
        )

    probe = _nav_probe(sessdata)
    if probe.data is None:
        # Error path — degrade to local-only, but report the *real* cause
        if probe.error == "network":
            cause = "网络/SSL 错误"
        elif probe.error is not None and probe.error.startswith("http:"):
            status = probe.error[5:]
            cause = f"B 站返回 HTTP {status}（可能被风控或接口变更）"
        else:
            cause = "未知错误"
        return ValidationResult(
            valid=True,
            messages=[
                ("warn", f"[警告] 无法在线验证 Cookie（{cause}），降级为本地格式校验"),
                ("ok", "[OK] 本地格式校验通过"),
            ],
        )
    # Success path — probe.data is not None
    data = probe.data
    if data.get("code") == 0 and data.get("data", {}).get("isLogin"):
        uname = data.get("data", {}).get("uname", "?")
        return ValidationResult(
            valid=True,
            messages=[("ok", f"[OK] Cookie 有效 | 已登录: {uname}")],
            uname=uname,
        )
    return ValidationResult(
        valid=False,
        messages=[("warn", "[提示] 现有 Cookie 已失效（服务端返回未登录）")],
    )


def ensure_cookie(cookie_dir: Optional[Path] = None) -> EnsureResult:
    """Ensure a valid Bilibili cookie is available; import from source if needed.

    Orchestration: validate → if invalid, import → re-validate.
    This encapsulates the cookie-readiness flow so the controller calls one
    function instead of coordinating internal module details.
    """
    msgs: list[tuple[MsgLevel, str]] = []

    result = validate(cookie_dir)
    if result.valid:
        return EnsureResult(ready=True, messages=result.messages)

    msgs.extend(result.messages)

    src = find_source(cookie_dir)
    if not src:
        return EnsureResult(ready=False, messages=msgs)

    imp = import_cookie(cookie_dir)
    msgs.extend(imp.messages)
    if not imp.success:
        return EnsureResult(ready=False, messages=msgs)

    result2 = validate(cookie_dir)
    msgs.extend(result2.messages)
    return EnsureResult(ready=result2.valid, messages=msgs)
