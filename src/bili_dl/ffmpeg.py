"""ffmpeg / ffprobe discovery and zero-loss audio (re)mux operations.

Two operations, both with ``-c:a copy`` (no re-encode, no quality loss):

* ``repair_audio_container`` —— remux an M4A into a standard ISOM container
  with ``+faststart`` (moov box moved to the front). The original bd.ps1
  applied this to the ``a`` (download-only) path; here we route BOTH the
  ``a`` path and the ``all`` path's extracted audio through it, so every
  produced M4A has the same byte-layout friendly to foobar2000 etc.

* ``extract_audio`` —— pull the first audio stream out of a downloaded MP4
  into an M4A, then standardise the container via repair_audio_container.
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from . import ui
from .config import REPAIR_MIN_SIZE_RATIO


def find_ffmpeg() -> Optional[str]:
    return shutil.which("ffmpeg")


def find_ffprobe() -> Optional[str]:
    return shutil.which("ffprobe")


def _run(args: list[str]) -> int:
    """Run ffmpeg/ffprobe; inherit stdout/stderr so progress bars show."""
    return subprocess.call(args)


def repair_audio_container(audio_path: Path, ffmpeg: str) -> bool:
    """Zero-copy remux to a standard faststart ISOM container.

    Returns True on success. On failure the original file is preserved.
    """
    if not audio_path.exists():
        ui.error("[失败] 待修复的音频文件不存在")
        return False

    temp_path = audio_path.with_name("_temp_" + Path(audio_path.name).stem + ".m4a")
    ui.info("[修复容器] 重新封装为标准 MP4 (faststart)...")
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
            ui.ok(f"[完成!] 容器已修复: {audio_path}")
            return True

    ui.error("[失败] 容器修复失败，保留原文件")
    if temp_path.exists():
        with contextlib.suppress(OSError):
            temp_path.unlink()
    return False


def extract_audio(video_path: Path, audio_dir: Path, ffmpeg: str) -> Optional[Path]:
    """Extract the first audio stream from a video into audio_dir/<name>.m4a.

    Only copies the stream (no re-encode); then standardises the container.
    Returns the final audio path on success, None on failure / no ffmpeg.
    """
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_path = audio_dir / f"{video_path.stem}.m4a"

    ui.info(f"[提取音频] {video_path.stem}")
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
        ui.error("[失败] 音频提取失败")
        return None

    # Standardise container for foobar2000 friendliness (same as the `a` path)
    if not repair_audio_container(audio_path, ffmpeg):
        return None
    return audio_path
