"""Durable, narrowly scoped state for Bilibili Web-session renewal.

The refresh token is deliberately kept out of ``config.toml``: it is a
credential, not a user preference.  A one-way SESSDATA fingerprint binds it
to the Cookie session it can refresh, preventing an old token from being used
after Cookie replacement.  An old token awaiting server confirmation also
stays here so a transient failure can be retried.  The state lives beside the
already sensitive ``cookies_bilibili.txt`` file, is written atomically, and
gets mode ``0600`` on POSIX.  On Windows the default AppData directory is
already per-user.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from .config import AUTH_STATE_FILENAME
from .paths import config_dir


@dataclass
class AuthState:
    """The credentials and throttle marker required by the renewal protocol."""

    refresh_token: str
    session_fingerprint: str
    last_refresh_check_utc: Optional[str] = None
    pending_confirm_token: Optional[str] = None


def path(cookie_dir: Optional[Path] = None) -> Path:
    """Return the credential-state path without creating it."""
    return (cookie_dir or config_dir()) / AUTH_STATE_FILENAME


def load(cookie_dir: Optional[Path] = None) -> tuple[Optional[AuthState], Optional[str]]:
    """Load a valid state file, without ever exposing its contents in errors."""
    state_path = path(cookie_dir)
    if not state_path.is_file():
        return None, None
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "刷新凭证状态文件不可读取"
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("refresh_token"), str)
        or not isinstance(raw.get("session_fingerprint"), str)
    ):
        return None, "刷新凭证状态文件格式无效"
    token = raw["refresh_token"]
    fingerprint = raw["session_fingerprint"]
    if not token or not fingerprint:
        return None, "刷新凭证状态文件格式无效"
    last_check = raw.get("last_refresh_check_utc")
    if last_check is not None and not isinstance(last_check, str):
        return None, "刷新凭证状态文件格式无效"
    pending_confirm = raw.get("pending_confirm_token")
    if pending_confirm is not None and (
        not isinstance(pending_confirm, str) or not pending_confirm
    ):
        return None, "刷新凭证状态文件格式无效"
    return AuthState(token, fingerprint, last_check, pending_confirm), None


def save(state: AuthState, cookie_dir: Optional[Path] = None) -> Optional[str]:
    """Atomically persist state; return a display-safe error on failure."""
    destination = path(cookie_dir)
    temp_path: Optional[Path] = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, raw_path = tempfile.mkstemp(
            prefix=f".{AUTH_STATE_FILENAME}.", suffix=".tmp", dir=destination.parent
        )
        temp_path = Path(raw_path)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            os.chmod(temp_path, 0o600)
            json.dump(asdict(state), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_path, destination)
        temp_path = None
    except OSError:
        return "无法保存刷新凭证状态"
    finally:
        if temp_path is not None:
            with contextlib.suppress(OSError):
                temp_path.unlink()
    return None


def remove(cookie_dir: Optional[Path] = None) -> Optional[str]:
    """Remove renewal state; return a display-safe error on failure."""
    try:
        path(cookie_dir).unlink(missing_ok=True)
    except OSError:
        return "无法清除旧刷新凭证状态"
    return None
