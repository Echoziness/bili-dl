"""yt-dlp invocation and download orchestration.

The original bd.ps1 had a scoping bug: ``$commonOpt`` was defined in the main
flow but referenced inside ``Invoke-DownloadVideo`` as ``@commonOpt``, which
PowerShell functions *can't* see by default — so the cookie/referer args
were silently dropped and 1080p downloads quietly degraded. Here the common
options are built from a :class:`DownloadConfig` dataclass threaded into the
call, so the bug class cannot recur.

Two-phase download (unchanged from bd.ps1):
  1. ``--print filename --skip-download`` to predict the final output path
     (without writing anything) so we can report it and locate the file
     for audio extraction.
  2. The real download with the same template/format, stdout inherited so
     yt-dlp's progress bar streams straight to the terminal.

This module is pure logic — it returns :class:`DownloadResult` objects and
never calls ``ui.*`` directly. The controller (``cli.py``) is responsible
for turning result messages into terminal output.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import ffmpeg as ff
from .config import FMT_AUDIO, FMT_AV, REFERER


@dataclass
class DownloadConfig:
    """All parameters for a single download, bundled to avoid 11-arg functions.

    Created by the controller from CLI options; passed as one object to
    :func:`download`. Adding a field here doesn't break existing call sites.
    """

    mode: str
    video_dir: Path
    audio_dir: Path
    cookie_path: Path
    proxy: str = ""
    insecure: bool = False
    ytdlp: Optional[str] = None
    ffmpeg_bin: Optional[str] = None
    referer: str = REFERER


@dataclass
class DownloadResult:
    """Outcome of a download + post-processing operation."""

    success: bool
    messages: list[tuple[str, str]] = field(default_factory=list)
    output_path: Optional[Path] = None


def find_ytdlp() -> Optional[str]:
    return shutil.which("yt-dlp")


def _template_for(mode: str, video_dir: Path, audio_dir: Path) -> str:
    if mode == "a":
        return str(audio_dir / "%(title)s.%(ext)s")
    return str(video_dir / "%(title)s.%(ext)s")


def _format_for(mode: str) -> str:
    return FMT_AUDIO if mode == "a" else FMT_AV


def _common_args(cfg: DownloadConfig) -> list[str]:
    """Build the yt-dlp args shared by phase 1 (predict) and phase 2 (download).

    Explicit parameter threading — the whole reason this project exists
    (see AGENTS.md §2.1: the PowerShell scoping bug that silently dropped
    these exact args).
    """
    args: list[str] = ["--no-playlist", "--cookies", str(cfg.cookie_path)]
    args += ["--add-header", f"Referer:{cfg.referer}"]
    if cfg.proxy:
        args += ["--proxy", cfg.proxy]
    if cfg.insecure:
        args.append("--no-check-certificate")
    return args


def download(url: str, cfg: DownloadConfig) -> DownloadResult:
    """Download one URL in the given mode; post-process as needed.

    Returns a :class:`DownloadResult`. ``cfg.ffmpeg_bin`` controls audio
    extraction / container repair; if ``None`` (no ffmpeg on PATH) those
    steps are skipped with a warning message.
    """
    ytdlp = cfg.ytdlp or find_ytdlp()
    if not ytdlp:
        return DownloadResult(
            success=False,
            messages=[("error", "[错误] 未找到 yt-dlp，请先安装 (pip install -U yt-dlp)")],
        )

    common = _common_args(cfg)
    tmpl = _template_for(cfg.mode, cfg.video_dir, cfg.audio_dir)
    fmt = _format_for(cfg.mode)
    merge = [] if cfg.mode == "a" else ["--merge-output-format", "mp4"]

    # Phase 1: predict output path (no download) -------------------------------
    predict = subprocess.run(
        [
            ytdlp,
            *common,
            "--print",
            "filename",
            "--skip-download",
            "-f",
            fmt,
            *merge,
            "-o",
            tmpl,
            url,
        ],
        capture_output=True,
        text=True,
    )
    if predict.returncode != 0 or not predict.stdout.strip():
        return DownloadResult(
            success=False,
            messages=[("error", "[失败] 无法获取视频信息")],
        )
    out_path = Path(predict.stdout.strip())

    # Phase 2: real download (inherit stdout/stderr for the progress bar) ----
    result = subprocess.run([ytdlp, *common, "-f", fmt, *merge, "-o", tmpl, url])

    if result.returncode != 0 or not out_path.exists():
        return DownloadResult(
            success=False,
            messages=[("error", "[失败]")],
        )

    # Post-processing ---------------------------------------------------------
    if cfg.mode == "a":
        msgs: list[tuple[str, str]] = [("ok", f"[完成!] 音频: {out_path}")]
        if cfg.ffmpeg_bin:
            repair = ff.repair_audio_container(out_path, cfg.ffmpeg_bin)
            msgs.extend(repair.messages)
        else:
            msgs.append(("warn", "[跳过] ffmpeg 不可用，跳过容器修复"))
        return DownloadResult(success=True, messages=msgs, output_path=out_path)

    # mode == "all" or "v" — video path
    video_msgs: list[tuple[str, str]] = [("ok", f"[完成!] 视频: {out_path}")]
    if cfg.mode == "all" and cfg.ffmpeg_bin:
        extract = ff.extract_audio(out_path, cfg.audio_dir, cfg.ffmpeg_bin)
        video_msgs.extend(extract.messages)
    elif cfg.mode == "all":
        video_msgs.append(("warn", "[跳过] ffmpeg 不可用，跳过音频提取"))
    return DownloadResult(success=True, messages=video_msgs, output_path=out_path)
