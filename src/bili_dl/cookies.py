"""Cookie management for Bilibili downloads.

Two responsibilities, mirroring the original bd.ps1:

1. ``import_bili_cookie`` —— scan the cookie directory for any ``.txt`` file
   containing ``bilibili`` entries (Netscape format), extract only those lines,
   fix the domain-match column for dot-prefixed domains (Netscape spec: must be
   TRUE), and write ``cookies_bilibili.txt``. No specific filename required —
   any ``.txt`` file with Bilibili cookie lines is auto-detected. Lines for
   other sites are never parsed, stored, or sent anywhere.

2. ``test_cookie_valid`` —— local format check (has SESSDATA under
   ``.bilibili.com``) plus an online probe against the ``nav`` API to verify
   the session is actually logged in. The probe must send a browser User-Agent
   (Bilibili returns 412 to urllib's default "Python-urllib/x.y" UA). On
   network/SSL failure we degrade gracefully to local-only validation,
   exactly like the PowerShell version.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import time
import urllib.request
from pathlib import Path
from typing import Optional

from . import ui
from .config import (
    BILI_COOKIE_FILENAME,
    NAV_API,
    NAV_TIMEOUT,
    USER_AGENT,
)
from .paths import config_dir


def bili_cookie_path(cookie_dir: Optional[Path] = None) -> Path:
    return (cookie_dir or config_dir()) / BILI_COOKIE_FILENAME


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def _extract_sessdata(lines: list[str]) -> Optional[str]:
    """Return the SESSDATA value from the first matching .bilibili.com line."""
    for raw_line in lines:
        line = raw_line.removeprefix("#HttpOnly_")
        if ".bilibili.com" in line and "SESSDATA" in line and not line.startswith("#"):
            fields = line.split("\t")
            if len(fields) >= 7 and fields[6]:
                return fields[6]
    return None


def _online_check(sessdata: str) -> Optional[bool]:
    """Probe the nav API; return True if logged in, False if not, None on error.

    ``None`` signals a network/SSL error —— caller should degrade to local
    validation rather than failing hard.
    """
    try:
        req = urllib.request.Request(
            NAV_API, headers={"Cookie": f"SESSDATA={sessdata}", "User-Agent": USER_AGENT}
        )
        with urllib.request.urlopen(req, timeout=NAV_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            return bool(data.get("code") == 0 and data.get("data", {}).get("isLogin"))
    except Exception:
        return None


def test_cookie_valid(cookie_dir: Optional[Path] = None) -> bool:
    """Validate the Bilibili cookie file.

    Returns True only when there is a usable cookie (local format OK and,
    if reachable, server-confirmed logged in).
    """
    path = bili_cookie_path(cookie_dir)
    if not path.exists():
        return False

    lines = _read_lines(path)
    if not lines:
        ui.warn("[提示] Cookie 文件为空")
        return False

    sessdata = _extract_sessdata(lines)
    if not sessdata:
        ui.warn("[提示] 未找到 SESSDATA（可能未登录，或 Cookie 已过期）")
        return False

    online = _online_check(sessdata)
    if online is True:
        # Fetch uname for friendly confirmation — reuse one more nav call only
        # in the success path; failure here doesn't change the verdict.
        try:
            req = urllib.request.Request(
                NAV_API, headers={"Cookie": f"SESSDATA={sessdata}", "User-Agent": USER_AGENT}
            )
            with urllib.request.urlopen(req, timeout=NAV_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
                uname = data.get("data", {}).get("uname", "?")
                ui.ok(f"[OK] Cookie 有效 | 已登录: {uname}")
        except Exception:
            ui.ok("[OK] Cookie 有效 | 已登录")
        return True
    if online is False:
        ui.warn("[提示] 现有 Cookie 已失效（服务端返回未登录）")
        return False
    # online is None —— network error, degrade
    ui.warn("[警告] 无法在线验证 Cookie（网络/SSL 错误），降级为本地格式校验")
    ui.ok("[OK] 本地格式校验通过")
    return True


def _find_src_cookie(cookie_dir: Optional[Path] = None) -> Optional[Path]:
    """Scan the cookie directory for any .txt file containing bilibili entries."""
    base = cookie_dir or config_dir()
    if not base.exists():
        return None
    candidates = sorted(
        p for p in base.glob("*.txt") if p.name != BILI_COOKIE_FILENAME
    )
    for src in candidates:
        for line in _read_lines(src):
            if "bilibili" in line and not line.removeprefix("#HttpOnly_").startswith("#"):
                if len(candidates) > 1:
                    ui.info(f"[摄取] 发现 {len(candidates)} 个 Cookie 文件，已使用 {src.name}")
                else:
                    ui.info(f"[摄取] 发现 {src.name}，正在提取 B 站 Cookie...")
                return src
    return None


def import_bili_cookie(cookie_dir: Optional[Path] = None) -> bool:
    """Auto-detect and extract .bilibili.com entries from any .txt file.

    Scans the cookie directory for any .txt file (except cookies_bilibili.txt)
    that contains bilibili cookie lines. The first match is used. Other-site
    cookies are neither parsed, stored, nor sent anywhere.

    Existing cookies_bilibili.txt is backed up before being overwritten.
    Returns True on success.
    """
    src = _find_src_cookie(cookie_dir)
    if not src:
        ui.error("[错误] 未找到包含 B 站 Cookie 的 .txt 文件")
        return False

    bili_lines = [
        line
        for line in _read_lines(src)
        if "bilibili" in line and not line.removeprefix("#HttpOnly_").startswith("#")
    ]
    if not bili_lines:
        ui.error(f"[错误] {src.name} 中未找到任何 bilibili 条目")
        return False

    dst = bili_cookie_path(cookie_dir)
    if dst.exists():
        bak = dst.with_name(f"{dst.name}.bak_{time.strftime('%Y%m%d_%H%M%S')}")
        with contextlib.suppress(OSError):
            shutil.copy2(dst, bak)

    # Fix Netscape column 2 (domain-match flag): dot-prefixed domains -> TRUE.
    # Also strip #HttpOnly_ prefix that some extensions add for HttpOnly cookies.
    out = ["# Netscape HTTP Cookie File"]
    for raw_line in bili_lines:
        line = raw_line.removeprefix("#HttpOnly_")
        fields = line.split("\t")
        if len(fields) >= 7 and fields[0].startswith(".") and fields[1] != "TRUE":
            fields[1] = "TRUE"
        out.append("\t".join(fields))

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(out) + "\n", encoding="utf-8")
    ui.ok(f"[摄取] 已提取 {len(bili_lines)} 条 B 站 Cookie -> {dst.name}")
    return True


def suspect_cookie_files(cookie_dir: Optional[Path] = None) -> list[Path]:
    """Return cookie-like .txt files that aren't the output file.

    Used to give the user a helpful hint when no valid cookie is found.
    """
    base = cookie_dir or config_dir()
    if not base.exists():
        return []
    return [p for p in base.glob("*.txt") if p.name != BILI_COOKIE_FILENAME]
