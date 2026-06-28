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
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
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


def _run(args: list[str]) -> int:
    """Run ffmpeg/ffprobe; inherit stdout/stderr so progress bars show."""
    return subprocess.call(args)


def repair_audio_container(audio_path: Path, ffmpeg: str) -> RepairResult:
    """Zero-copy remux to a standard faststart ISOM container.

    Returns a :class:`RepairResult`. On failure the original file is preserved.
    """
    if not audio_path.exists():
        return RepairResult(
            success=False,
            messages=[("error", "[失败] 待修复的音频文件不存在")],
        )

    temp_path = audio_path.with_name("_temp_" + Path(audio_path.name).stem + ".m4a")
    rc = _run(
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
        orig_size = audio_path.stat().st_size
        new_size = temp_path.stat().st_size
        if new_size > orig_size * REPAIR_MIN_SIZE_RATIO:
            temp_path.replace(audio_path)
            return RepairResult(
                success=True,
                messages=[("ok", f"[完成!] 容器已修复: {audio_path}")],
            )

    if temp_path.exists():
        with contextlib.suppress(OSError):
            temp_path.unlink()
    return RepairResult(
        success=False,
        messages=[("error", "[失败] 容器修复失败，保留原文件")],
    )


def extract_audio(video_path: Path, audio_dir: Path, ffmpeg: str) -> ExtractResult:
    """Extract the first audio stream from a video into audio_dir/<name>.m4a.

    Only copies the stream (no re-encode); then standardises the container.
    """
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_path = audio_dir / f"{video_path.stem}.m4a"

    rc = _run(
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
        return ExtractResult(
            success=False,
            messages=[("error", "[失败] 音频提取失败")],
        )

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
