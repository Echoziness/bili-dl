"""ffmpeg / ffprobe discovery and zero-loss audio (re)mux operations.

Two operations, both with ``-c:a copy`` (no re-encode, no quality loss):

* ``repair_audio_container`` — remux an M4A into a standard ISOM container
  with ``+faststart`` (moov box moved to the front). The original bd.ps1
  applied this to the ``a`` (download-only) path; here we route BOTH the
  ``a`` path and the ``all`` path's extracted audio through it, so every
  produced M4A has the same byte-layout friendly to foobar2000 etc.

* ``extract_audio`` — pull the first audio stream out of a downloaded MP4
  into an M4A, then standardise the container via repair_audio_container.

This module is pure logic — it returns :class:`RepairResult` /
:class:`ExtractResult` objects and never calls ``ui.*`` directly. The
controller (``cli.py``) is responsible for turning result messages into
terminal output.

Robustness: all ffmpeg invocations capture stderr. On failure, the actual
ffmpeg error text is included in the result message so the user knows
*why* it failed — not just *that* it failed. All filesystem operations
(mkdir, replace, stat, unlink) are wrapped in try/except to prevent
unhandled crashes from transient OS errors (WinError 32 file locking,
permission denied, disk full, etc.).
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config import REPAIR_MIN_SIZE_RATIO


@dataclass
class RepairResult:
    """Outcome of an audio container repair."""

    success: bool
    messages: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class ExtractResult:
    """Outcome of an audio extraction + container repair."""

    success: bool
    messages: list[tuple[str, str]] = field(default_factory=list)
    audio_path: Optional[Path] = None


def find_ffmpeg() -> Optional[str]:
    return shutil.which("ffmpeg")


def _run(args: list[str]) -> tuple[int, str]:
    """Run ffmpeg; return (returncode, stderr_text).

    Captures stderr so that on failure the actual ffmpeg error can be
    included in the result message — the user sees *why* ffmpeg failed,
    not just *that* it failed.
    """
    result = subprocess.run(args, capture_output=True, text=True)
    return result.returncode, result.stderr.strip()


def _stderr_detail(stderr: str) -> str:
    """Format stderr for inclusion in error messages."""
    if not stderr:
        return ""
    # ffmpeg stderr can be multi-line; take the last meaningful line
    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
    if lines:
        return f": {lines[-1]}"
    return ""


def _cfa_enabled() -> bool:
    """True if Windows Controlled Folder Access is enabled (registry probe).

    Returns False on non-Windows, on read errors, or when the registry
    value is missing/not 1. Best-effort — never raises.
    """
    if sys.platform != "win32":
        return False
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows Defender\Windows Defender Exploit Guard"
            r"\Controlled Folder Access",
        )
        try:
            value, _ = winreg.QueryValueEx(key, "EnableControlledFolderAccess")
            return int(value) == 1
        finally:
            winreg.CloseKey(key)
    except OSError:
        return False


def _cfa_hint(path: Path) -> Optional[str]:
    """Return a targeted hint when *path* couldn't be written and the cause
    is plausibly Windows Controlled Folder Access (CFA).

    CFA blocks unsigned apps from writing library folders (~/Videos, ~/Music)
    and *silently disguises* the denial as ``No such file or directory`` —
    the parent dir exists and is writable, yet the file is never created.
    We detect exactly that signature: parent exists + writable + CFA enabled.
    Returns a message for the user or ``None`` to stay quiet.
    """
    if sys.platform != "win32":
        return None
    parent = path.parent
    if not parent.exists() or not os.access(parent, os.W_OK):
        return None
    if not _cfa_enabled():
        return None
    return (
        "[提示] 目标目录存在且可写，但文件未能创建 —— 可能被 Windows 受控文件夹访问"
        "（Controlled Folder Access）拦截（常见于 ffmpeg 升级后新二进制未被放行）。"
        "请将 ffmpeg/yt-dlp 加入白名单，见 README「Windows: Controlled Folder Access」。"
    )


def repair_audio_container(audio_path: Path, ffmpeg: str) -> RepairResult:
    """Zero-copy remux to a standard faststart ISOM container.

    Returns a :class:`RepairResult`. On failure the original file is preserved.
    """
    if not audio_path.exists():
        return RepairResult(
            success=False,
            messages=[("error", f"[失败] 待修复的音频文件不存在: {audio_path}")],
        )

    # Random suffix avoids collisions when two repairs on the same stem run
    # concurrently (e.g. same title extracted from video and downloaded as audio).
    temp_path = audio_path.with_name(
        f"_temp_{Path(audio_path.name).stem}_{uuid.uuid4().hex[:8]}.m4a"
    )
    rc, stderr = _run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(audio_path),
            "-map",
            "0:a",
            "-c:a",
            "copy",
            "-map_metadata",
            "0",
            "-movflags",
            "+faststart",
            str(temp_path),
        ]
    )

    if rc == 0 and temp_path.exists():
        try:
            orig_size = audio_path.stat().st_size
            new_size = temp_path.stat().st_size
        except OSError as e:
            return RepairResult(
                success=False,
                messages=[("error", f"[失败] 无法读取文件大小: {e}")],
            )
        if new_size > orig_size * REPAIR_MIN_SIZE_RATIO:
            try:
                temp_path.replace(audio_path)
            except OSError as e:
                # WinError 32 (file locked) or permission denied
                with contextlib.suppress(OSError):
                    temp_path.unlink()
                return RepairResult(
                    success=False,
                    messages=[("error", f"[失败] 容器修复失败（无法替换文件: {e}），保留原文件")],
                )
            return RepairResult(
                success=True,
                messages=[("ok", f"[完成!] 容器已修复: {audio_path}")],
            )

    if temp_path.exists():
        with contextlib.suppress(OSError):
            temp_path.unlink()
    msgs: list[tuple[str, str]] = [
        ("error", f"[失败] 容器修复失败{_stderr_detail(stderr)}，保留原文件")
    ]
    hint = _cfa_hint(audio_path)
    if hint:
        msgs.append(("warn", hint))
    return RepairResult(success=False, messages=msgs)


def extract_audio(video_path: Path, audio_dir: Path, ffmpeg: str) -> ExtractResult:
    """Extract the first audio stream from a video into audio_dir/<name>.m4a.

    Only copies the stream (no re-encode); then standardises the container.
    """
    try:
        audio_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return ExtractResult(
            success=False,
            messages=[("error", f"[失败] 无法创建音频目录 {audio_dir}: {e}")],
        )
    audio_path = audio_dir / f"{video_path.stem}.m4a"

    rc, stderr = _run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video_path),
            "-map",
            "0:a:0?",
            "-vn",
            "-c:a",
            "copy",
            "-map_metadata",
            "0",
            str(audio_path),
        ]
    )

    if rc != 0 or not audio_path.exists():
        msgs: list[tuple[str, str]] = [("error", f"[失败] 音频提取失败{_stderr_detail(stderr)}")]
        hint = _cfa_hint(audio_path)
        if hint:
            msgs.append(("warn", hint))
        return ExtractResult(success=False, messages=msgs)

    # Standardise container for foobar2000 friendliness (same as the `a` path)
    repair = repair_audio_container(audio_path, ffmpeg)
    if not repair.success:
        return ExtractResult(
            success=False,
            messages=[("info", f"[提取音频] {video_path.stem}"), *repair.messages],
        )
    return ExtractResult(
        success=True,
        messages=[("info", f"[提取音频] {video_path.stem}"), *repair.messages],
        audio_path=audio_path,
    )
