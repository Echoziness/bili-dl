"""Bilibili Cookie persistence, validation, and renewal orchestration.

This is the storage boundary for the active Web session. It validates imported
or QR-issued Cookies, atomically replaces verified sessions, binds renewal
state, and serializes renewal transactions. Protocol details stay in
:mod:`authrefresh`; HTTP mechanics stay in :mod:`transport`.

This module is pure logic — it returns :class:`ValidationResult` /
:class:`EnsureResult` objects and never calls ``ui.*`` directly. The controller
(``cli.py``) is responsible for turning result messages into terminal output.

Online probe: the ``nav`` API requires the browser headers supplied by the
shared transport (Bilibili returns HTTP 412 to urllib's default UA). On a
transport failure we degrade gracefully to local-only validation.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

from filelock import FileLock
from filelock import Timeout as FileLockTimeout

from . import authrefresh, authstate, transport
from .config import (
    AUTH_LOCK_FILENAME,
    AUTH_LOCK_TIMEOUT,
    BILI_COOKIE_FILENAME,
    NAV_API,
)
from .cookiesource import find_source, import_cookie, read_lines
from .paths import config_dir


@dataclass
class ValidationResult:
    """Outcome of a cookie validity check."""

    valid: bool
    messages: list[tuple[str, str]] = field(default_factory=list)
    uname: Optional[str] = None


@dataclass
class EnsureResult:
    """Outcome of the full validate → import → re-validate flow."""

    ready: bool
    messages: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class QrStoreResult:
    """Outcome of validating and atomically storing a QR-login cookie set."""

    success: bool
    messages: list[tuple[str, str]] = field(default_factory=list)


def bili_cookie_path(cookie_dir: Optional[Path] = None) -> Path:
    return (cookie_dir or config_dir()) / BILI_COOKIE_FILENAME


def _auth_lock(cookie_dir: Optional[Path]) -> FileLock:
    """Return the cross-process lock protecting Cookie/renewal state updates."""
    lock_path = (cookie_dir or config_dir()) / AUTH_LOCK_FILENAME
    return FileLock(lock_path, timeout=AUTH_LOCK_TIMEOUT)


def _extract_cookie_value(lines: list[str], name: str) -> Optional[str]:
    """Return a named Cookie value from the first matching bilibili.com line.

    Matches by Netscape column 6 rather than a substring test on the whole
    line. Domain column must contain ``bilibili.com``.
    """
    for raw_line in lines:
        line = raw_line.removeprefix("#HttpOnly_")
        if line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) >= 7 and "bilibili.com" in fields[0] and fields[5] == name and fields[6]:
            return fields[6]
    return None


def _extract_sessdata(lines: list[str]) -> Optional[str]:
    return _extract_cookie_value(lines, "SESSDATA")


def _session_fingerprint(lines: list[str]) -> Optional[str]:
    """Return a non-secret identifier binding renewal state to one session."""
    sessdata = _extract_sessdata(lines)
    if not sessdata:
        return None
    return hashlib.sha256(sessdata.encode("utf-8")).hexdigest()


def _current_session_fingerprint(cookie_dir: Optional[Path]) -> Optional[str]:
    return _session_fingerprint(read_lines(bili_cookie_path(cookie_dir)))


def renewal_state(
    cookie_dir: Optional[Path] = None,
) -> tuple[Optional[authstate.AuthState], Optional[str]]:
    """Load renewal state only when it belongs to the current Cookie session."""
    state, error = authstate.load(cookie_dir)
    if state is None:
        return None, error
    fingerprint = _current_session_fingerprint(cookie_dir)
    if fingerprint is None:
        return None, "当前 Cookie 缺少可识别的会话信息"
    if fingerprint != state.session_fingerprint:
        return None, "刷新凭证不属于当前 Cookie 会话，请重新运行 bili-dl login"
    if _extract_cookie_value(read_lines(bili_cookie_path(cookie_dir)), "bili_jct") is None:
        return None, "当前 Cookie 缺少 bili_jct，无法自动续期"
    return state, None


def _nav_probe(
    sessdata: str, *, proxy: Optional[str] = None
) -> tuple[Optional[dict[str, Any]], Optional[transport.HttpFailure]]:
    """Probe the nav API through the shared authenticated HTTP boundary."""
    opener = transport.cookie_opener(proxy=proxy)
    req = transport.request(NAV_API, headers={"Cookie": f"SESSDATA={sessdata}"})
    return transport.fetch_json(opener, req)


def validate(cookie_dir: Optional[Path] = None, *, proxy: Optional[str] = None) -> ValidationResult:
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

    data, failure = _nav_probe(sessdata, proxy=proxy)
    if data is None:
        # Error path — degrade to local-only, but report the *real* cause
        if failure is not None and failure.kind == "network":
            cause = "网络/SSL 错误"
        elif failure is not None and failure.kind == "bad_json":
            cause = "B 站返回非 JSON 内容（可能被风控或接口变更）"
        elif failure is not None and failure.kind == "http" and failure.status is not None:
            cause = f"B 站返回 HTTP {failure.status}（可能被风控或接口变更）"
        elif failure is not None:
            cause = failure.describe()
        else:
            cause = "未知错误"
        return ValidationResult(
            valid=True,
            messages=[
                ("warn", f"[警告] 无法在线验证 Cookie（{cause}），降级为本地格式校验"),
                ("ok", "[OK] 本地格式校验通过"),
            ],
        )
    # Success path — data is not None
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


def _store_verified_cookie(
    cookie_lines: list[str],
    cookie_dir: Optional[Path],
    success_message: str,
    *,
    proxy: Optional[str] = None,
) -> QrStoreResult:
    """Validate a replacement session online, then atomically store it.

    The previous cookie file remains untouched unless Bilibili's ``nav`` API
    confirms the newly received session.  Unlike :func:`validate`, this does
    not degrade on a network error: a new credential must be proven before it
    can overwrite a known-good one.
    """
    sessdata = _extract_sessdata(cookie_lines)
    if not sessdata:
        return QrStoreResult(False, [("error", "[登录] 新会话缺少 SESSDATA，未改动现有 Cookie")])

    data, failure = _nav_probe(sessdata, proxy=proxy)
    if data is None:
        detail = failure.describe() if failure else "未知错误"
        return QrStoreResult(
            False, [("error", f"[登录] 无法验证新会话（{detail}），未改动现有 Cookie")]
        )
    if data.get("code") != 0 or not data.get("data", {}).get("isLogin"):
        return QrStoreResult(False, [("error", "[登录] 新会话未登录，未改动现有 Cookie")])

    destination = bili_cookie_path(cookie_dir)
    temp_path: Optional[Path] = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, raw_path = tempfile.mkstemp(
            prefix=f".{BILI_COOKIE_FILENAME}.", suffix=".tmp", dir=destination.parent
        )
        temp_path = Path(raw_path)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(cookie_lines) + "\n")
        os.replace(temp_path, destination)
        temp_path = None
    except OSError as exc:
        return QrStoreResult(False, [("error", f"[登录] 无法保存新 Cookie：{exc}")])
    finally:
        if temp_path is not None:
            with contextlib.suppress(OSError):
                temp_path.unlink()

    uname = data.get("data", {}).get("uname", "?")
    return QrStoreResult(True, [("ok", success_message.format(uname=uname))])


def store_qr_cookie(
    cookie_lines: list[str], cookie_dir: Optional[Path] = None, *, proxy: Optional[str] = None
) -> QrStoreResult:
    """Validate QR-login cookies online, then atomically replace the output file."""
    return _store_verified_cookie(
        cookie_lines, cookie_dir, "[登录] 成功 | 已登录: {uname}", proxy=proxy
    )


def store_qr_session(
    cookie_lines: list[str],
    refresh_token: Optional[str],
    cookie_dir: Optional[Path] = None,
    *,
    proxy: Optional[str] = None,
) -> QrStoreResult:
    """Store a verified QR session and its renewal credential together.

    The cookie remains usable if writing the optional renewal state fails; the
    result explicitly reports that automatic renewal is unavailable instead
    of pretending that the credential was durably stored.
    """
    try:
        with _auth_lock(cookie_dir):
            return _store_qr_session_locked(cookie_lines, refresh_token, cookie_dir, proxy)
    except (FileLockTimeout, OSError):
        return QrStoreResult(False, [("error", "[登录] 另一项登录或续期操作正在进行，请稍后重试")])


def _store_qr_session_locked(
    cookie_lines: list[str],
    refresh_token: Optional[str],
    cookie_dir: Optional[Path],
    proxy: Optional[str],
) -> QrStoreResult:
    result = store_qr_cookie(cookie_lines, cookie_dir, proxy=proxy)
    if not result.success:
        return result
    fingerprint = _session_fingerprint(cookie_lines)
    if fingerprint is None:
        error = authstate.remove(cookie_dir)
        result.messages.append(("warn", "[登录] 无法识别新会话，自动续期未启用"))
        if error:
            result.messages.append(("warn", f"[登录] {error}"))
        return result
    if not refresh_token:
        error = authstate.remove(cookie_dir)
        result.messages.append(("warn", "[登录] B 站未返回续期凭证，需在会话失效后重新扫码"))
        if error:
            result.messages.append(("warn", f"[登录] {error}"))
        return result
    if _extract_cookie_value(cookie_lines, "bili_jct") is None:
        error = authstate.remove(cookie_dir)
        result.messages.append(("warn", "[登录] 新会话缺少 bili_jct，自动续期未启用"))
        if error:
            result.messages.append(("warn", f"[登录] {error}"))
        return result
    error = authstate.save(authstate.AuthState(refresh_token, fingerprint), cookie_dir)
    if error:
        clear_error = authstate.remove(cookie_dir)
        result.messages.append(("warn", f"[登录] 会话已保存，但自动续期不可用：{error}"))
        if clear_error:
            result.messages.append(("warn", f"[登录] {clear_error}"))
    else:
        result.messages.append(("info", "[登录] 已启用每日会话续期检查"))
    return result


def _utc_today() -> str:
    """Use a UTC calendar day for the once-per-day remote renewal check."""
    return datetime.now(UTC).date().isoformat()


def _renew_if_due(
    cookie_dir: Optional[Path], *, proxy: Optional[str] = None
) -> list[tuple[str, str]]:
    """Serialize and run the failure-tolerant daily renewal transaction."""
    try:
        with _auth_lock(cookie_dir):
            return _renew_if_due_locked(cookie_dir, proxy)
    except (FileLockTimeout, OSError):
        return [("warn", "[登录] 另一项登录或续期操作正在进行，本次跳过自动续期")]


def _renew_if_due_locked(cookie_dir: Optional[Path], proxy: Optional[str]) -> list[tuple[str, str]]:
    """Refresh a verified session only when Bilibili requests it for the day."""
    state, error = renewal_state(cookie_dir)
    if error:
        return [("warn", f"[登录] 自动续期不可用：{error}")]
    if state is None:
        return []

    cookie_path = bili_cookie_path(cookie_dir)
    messages: list[tuple[str, str]] = []
    if state.pending_confirm_token is not None:
        error = authrefresh.confirm_pending(cookie_path, state.pending_confirm_token, proxy=proxy)
        if error:
            return [("warn", f"[登录] 无法完成上次会话续期确认：{error}")]
        state = authstate.AuthState(
            state.refresh_token, state.session_fingerprint, state.last_refresh_check_utc
        )
        error = authstate.save(state, cookie_dir)
        if error:
            return [("warn", f"[登录] 已确认上次会话续期，但无法更新本地状态：{error}")]
        messages.append(("info", "[登录] 已完成上次会话续期确认"))

    if state.last_refresh_check_utc == _utc_today():
        return messages

    renewal = authrefresh.check_and_refresh(cookie_path, state.refresh_token, proxy=proxy)
    messages.extend(renewal.messages)
    if not renewal.checked:
        return messages
    if not renewal.refreshed:
        error = authstate.save(
            authstate.AuthState(state.refresh_token, state.session_fingerprint, _utc_today()),
            cookie_dir,
        )
        if error:
            messages.append(("warn", f"[登录] 无法记录每日续期检查：{error}"))
        return messages

    stored = _store_verified_cookie(
        renewal.cookie_lines,
        cookie_dir,
        "[登录] 会话已自动刷新 | 已登录: {uname}",
        proxy=proxy,
    )
    messages.extend(stored.messages)
    if not stored.success or renewal.refresh_token is None:
        return messages
    fingerprint = _session_fingerprint(renewal.cookie_lines)
    if fingerprint is None:
        messages.append(("warn", "[登录] 新会话无法绑定续期凭证，自动续期不可用"))
        return messages
    error = authstate.save(
        authstate.AuthState(
            renewal.refresh_token,
            fingerprint,
            _utc_today(),
            pending_confirm_token=renewal.old_refresh_token,
        ),
        cookie_dir,
    )
    if error:
        clear_error = authstate.remove(cookie_dir)
        messages.append(("warn", f"[登录] 新会话已保存，但自动续期不可用：{error}"))
        if clear_error:
            messages.append(("warn", f"[登录] {clear_error}"))
        return messages
    error = authrefresh.confirm(renewal)
    if error:
        messages.append(("warn", f"[登录] 新会话已保存，但无法确认旧续期凭证：{error}"))
        return messages
    error = authstate.save(
        authstate.AuthState(renewal.refresh_token, fingerprint, _utc_today()), cookie_dir
    )
    if error:
        messages.append(("warn", f"[登录] 续期已确认，但无法清除待确认状态：{error}"))
    return messages


def ensure_cookie(
    cookie_dir: Optional[Path] = None, *, proxy: Optional[str] = None
) -> EnsureResult:
    """Ensure a valid Bilibili cookie is available; import from source if needed.

    Orchestration: validate → if invalid, import → re-validate.
    This encapsulates the cookie-readiness flow so the controller calls one
    function instead of coordinating internal module details.
    """
    msgs: list[tuple[str, str]] = []

    result = validate(cookie_dir, proxy=proxy)
    if result.valid:
        return EnsureResult(
            ready=True,
            messages=result.messages + _renew_if_due(cookie_dir, proxy=proxy),
        )

    msgs.extend(result.messages)

    src = find_source(cookie_dir)
    if not src:
        return EnsureResult(ready=False, messages=msgs)

    imp = import_cookie(cookie_dir)
    msgs.extend(imp.messages)
    if not imp.success:
        return EnsureResult(ready=False, messages=msgs)

    result2 = validate(cookie_dir, proxy=proxy)
    msgs.extend(result2.messages)
    return EnsureResult(ready=result2.valid, messages=msgs)
