"""Cookie source detection and import.

Single responsibility: find a ``.txt`` file containing Bilibili cookie entries
and extract only those lines into ``cookies_bilibili.txt``.

This module is pure logic — it returns :class:`ImportResult` objects and never
calls ``ui.*`` directly. The controller (``cli.py``) is responsible for turning
result messages into terminal output.

Privacy invariant: only lines containing ``"bilibili"`` are extracted; other-site
cookies are never parsed, stored, or sent anywhere.
"""

from __future__ import annotations

import contextlib
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config import BILI_COOKIE_FILENAME
from .paths import config_dir


@dataclass
class ImportResult:
    """Outcome of a cookie import attempt."""

    success: bool
    messages: list[tuple[str, str]] = field(default_factory=list)
    count: int = 0
    source: Optional[Path] = None


def read_lines(path: Path) -> list[str]:
    """Read cookie file lines; return ``[]`` on I/O error."""
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def _strip_httponly(line: str) -> str:
    """Remove the ``#HttpOnly_`` prefix that some browser extensions add."""
    return line.removeprefix("#HttpOnly_")


def is_bili_line(line: str) -> bool:
    """True if *line* is a Bilibili cookie entry (not a comment, not other-site)."""
    return "bilibili" in line and not _strip_httponly(line).startswith("#")


def _candidates(cookie_dir: Optional[Path] = None) -> list[Path]:
    """Sorted ``.txt`` files in *cookie_dir*, excluding the output file."""
    base = cookie_dir or config_dir()
    if not base.exists():
        return []
    return sorted(p for p in base.glob("*.txt") if p.name != BILI_COOKIE_FILENAME)


def _first_bili_source(cookie_dir: Optional[Path] = None) -> Optional[Path]:
    """First candidate ``.txt`` file containing Bilibili entries, or ``None``.

    Shared by :func:`find_source` and :func:`import_cookie` so the scan logic
    lives in one place (previously duplicated as inline loops).
    """
    for c in _candidates(cookie_dir):
        if any(is_bili_line(line) for line in read_lines(c)):
            return c
    return None


def find_source(cookie_dir: Optional[Path] = None) -> Optional[Path]:
    """Scan *cookie_dir* for the first ``.txt`` file with Bilibili entries.

    The output file ``cookies_bilibili.txt`` is excluded from candidates.
    Returns the source path or ``None`` if nothing was found.
    """
    return _first_bili_source(cookie_dir)


def import_cookie(cookie_dir: Optional[Path] = None, dest: Optional[Path] = None) -> ImportResult:
    """Auto-detect and extract Bilibili entries from any ``.txt`` file.

    Scans the cookie directory for a source file, extracts only Bilibili-domain
    lines, fixes the Netscape domain-match column, and writes the result to
    *dest* (defaults to ``cookies_bilibili.txt`` in the cookie directory).
    Existing output is backed up before overwrite.
    """
    candidates = _candidates(cookie_dir)
    src = _first_bili_source(cookie_dir)
    if not src:
        return ImportResult(
            success=False,
            messages=[("error", "[错误] 未找到包含 B 站 Cookie 的 .txt 文件")],
        )

    msgs: list[tuple[str, str]] = []
    if len(candidates) > 1:
        msgs.append(("info", f"[摄取] 发现 {len(candidates)} 个 Cookie 文件，已使用 {src.name}"))
    else:
        msgs.append(("info", f"[摄取] 发现 {src.name}，正在提取 B 站 Cookie..."))

    bili_lines = [line for line in read_lines(src) if is_bili_line(line)]
    if not bili_lines:
        msgs.append(("error", f"[错误] {src.name} 中未找到任何 bilibili 条目"))
        return ImportResult(success=False, messages=msgs, source=src)

    dst = dest or (cookie_dir or config_dir()) / BILI_COOKIE_FILENAME
    if dst.exists():
        bak = dst.with_name(f"{dst.name}.bak_{time.strftime('%Y%m%d_%H%M%S')}")
        with contextlib.suppress(OSError):
            shutil.copy2(dst, bak)

    # Fix Netscape column 2 (domain-match flag): dot-prefixed domains -> TRUE.
    # Also strip #HttpOnly_ prefix that some extensions add for HttpOnly cookies.
    out = ["# Netscape HTTP Cookie File"]
    for raw_line in bili_lines:
        line = _strip_httponly(raw_line)
        fields = line.split("\t")
        if len(fields) >= 7 and fields[0].startswith(".") and fields[1] != "TRUE":
            fields[1] = "TRUE"
        out.append("\t".join(fields))

    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        dst.write_text("\n".join(out) + "\n", encoding="utf-8")
    except OSError as e:
        msgs.append(("error", f"[错误] 无法写入 Cookie 文件 {dst}: {e}"))
        return ImportResult(success=False, messages=msgs, count=len(bili_lines), source=src)
    msgs.append(("ok", f"[摄取] 已提取 {len(bili_lines)} 条 B 站 Cookie -> {dst.name}"))
    return ImportResult(success=True, messages=msgs, count=len(bili_lines), source=src)
