"""Bundled ffmpeg discovery and zero-loss audio (re)mux operations.

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
import struct
import subprocess
import sys
import uuid
from collections.abc import Iterator
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
    """Resolve an explicit override, bundled ffmpeg, then supported fallbacks.

    imageio-ffmpeg validates its bundled / Conda / PATH candidates. Its explicit
    environment override is unchecked, so validate that separately and never
    silently replace an invalid user choice with another binary.
    """
    override = os.environ.get("IMAGEIO_FFMPEG_EXE")
    if override:
        executable = shutil.which(override)
        if not executable:
            return None
        try:
            result = subprocess.run(
                [executable, "-version"],
                capture_output=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return executable if result.returncode == 0 else None

    from imageio_ffmpeg import get_ffmpeg_exe

    try:
        candidate: str = get_ffmpeg_exe()
    except RuntimeError:
        return None
    # The package's final fallback may be a bare command. yt-dlp requires an
    # existing path for --ffmpeg-location, so resolve it here as well.
    return shutil.which(candidate)


def _run(args: list[str]) -> tuple[int, str]:
    """Run ffmpeg; return (returncode, stderr_text).

    Captures stderr so that on failure the actual ffmpeg error can be
    included in the result message — the user sees *why* ffmpeg failed,
    not just *that* it failed.
    """
    result = subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
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


def _audio_muxer_args(source: Path) -> list[str]:
    """Use MP4, retaining FLAC tail packets without changing AAC priming edits.

    Some Bilibili fragmented FLAC tracks report zero packet durations. An MP4
    edit list can then hide the final audio packet. FLAC needs no encoder-delay
    edit; AAC does, so inspect the structured sample entry rather than disabling
    edits globally or parsing FFmpeg diagnostics. No ffprobe is required.
    """
    args = ["-f", "mp4"]
    try:
        with source.open("rb") as stream:

            def boxes(start: int, end: int) -> Iterator[tuple[bytes, int, int]]:
                while start + 8 <= end:
                    stream.seek(start)
                    header = stream.read(8)
                    if len(header) != 8:
                        return
                    size, kind = struct.unpack(">I4s", header)
                    header_size = 8
                    if size == 1:
                        large = stream.read(8)
                        if len(large) != 8:
                            return
                        size = struct.unpack(">Q", large)[0]
                        header_size = 16
                    elif size == 0:
                        size = end - start
                    if size < header_size or start + size > end:
                        return
                    yield kind, start + header_size, start + size
                    start += size

            pending = [(0, source.stat().st_size)]
            while pending:
                for kind, payload, end in boxes(*pending.pop()):
                    if kind in {b"moov", b"trak", b"mdia", b"minf", b"stbl"}:
                        pending.append((payload, end))
                    elif kind == b"stsd":
                        # FullBox flags/version and sample-entry count.
                        if any(tag == b"fLaC" for tag, _, _ in boxes(payload + 8, end)):
                            return [*args, "-use_editlist", "0"]
    except OSError:
        # Let FFmpeg report unreadable/non-ISO-BMFF inputs through its usual
        # failure path; the original-file preservation contract still applies.
        pass
    return args


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
            # .m4a otherwise selects the restricted iPod muxer, which rejects
            # Bilibili's Hi-Res FLAC tracks. Keep the codec and use ISO BMFF.
            *_audio_muxer_args(audio_path),
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
            *_audio_muxer_args(video_path),
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


def repair_flac_video_container(video_path: Path, ffmpeg: str) -> RepairResult:
    """Expose FLAC tail packets hidden by a merger's MP4 edit list.

    Let FFmpeg rebuild the container and preserve both streams. Negative CTS
    offsets retain B-frame presentation without requiring video edit lists.
    Ordinary AAC videos keep their existing edit/encoder-delay handling.
    """
    muxer_args = _audio_muxer_args(video_path)
    if "-use_editlist" not in muxer_args:
        return RepairResult(success=True)
    temporary = video_path.with_name(f"_temp_{video_path.stem}_{uuid.uuid4().hex[:8]}.mp4")
    try:
        rc, stderr = _run(
            [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ignore_editlist",
                "1",
                "-i",
                str(video_path),
                "-map",
                "0",
                "-c",
                "copy",
                "-map_metadata",
                "0",
                "-movflags",
                "+faststart+negative_cts_offsets",
                *muxer_args,
                str(temporary),
            ]
        )
        if rc == 0 and temporary.exists():
            if temporary.stat().st_size > video_path.stat().st_size * REPAIR_MIN_SIZE_RATIO:
                temporary.replace(video_path)
                return RepairResult(
                    success=True, messages=[("ok", "[完成!] 视频内 FLAC 音轨容器已整理")]
                )
        detail = _stderr_detail(stderr)
    except OSError as error:
        detail = f": {error}"
    finally:
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)
    return RepairResult(
        success=False, messages=[("error", f"[失败] 视频音轨容器整理失败{detail}，保留原文件")]
    )
