"""yt-dlp invocation and download orchestration.

The original bd.ps1 had a scoping bug: ``$commonOpt`` was defined in the main
flow but referenced inside ``Invoke-DownloadVideo`` as ``@commonOpt``, which
PowerShell functions *can't* see by default — so the cookie/referer args
were silently dropped and 1080p downloads quietly degraded. Here the common
options are an explicit ``list[str]`` threaded into every call, so the bug
class cannot recur.

Two-phase download (unchanged from bd.ps1):
  1. ``--print filename --skip-download`` to predict the final output path
     (without writing anything) so we can report it and locate the file
     for audio extraction.
  2. The real download with the same template/format, stdout inherited so
     yt-dlp's progress bar streams straight to the terminal.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional

from . import ffmpeg as ff
from . import ui
from .config import FMT_AUDIO, FMT_AV


def find_ytdlp() -> Optional[str]:
    return shutil.which("yt-dlp")


def _template_for(mode: str, video_dir: Path, audio_dir: Path) -> str:
    if mode == "a":
        return str(audio_dir / "%(title)s.%(ext)s")
    return str(video_dir / "%(title)s.%(ext)s")


def _format_for(mode: str) -> str:
    return FMT_AUDIO if mode == "a" else FMT_AV


def _common_args(
    cookie_path: Path,
    referer: str,
    proxy: str,
    insecure: bool,
) -> list[str]:
    args: list[str] = ["--no-playlist", "--cookies", str(cookie_path)]
    args += ["--add-header", f"Referer:{referer}"]
    if proxy:
        args += ["--proxy", proxy]
    if insecure:
        args.append("--no-check-certificate")
    return args


def download(
    url: str,
    mode: str,
    *,
    video_dir: Path,
    audio_dir: Path,
    cookie_path: Path,
    referer: str,
    proxy: str = "",
    insecure: bool = False,
    ytdlp: Optional[str] = None,
    ffmpeg_bin: Optional[str] = None,
) -> bool:
    """Download one URL in the given mode; post-process as needed.

    Returns True on success. ``ffmpeg_bin`` controls audio extraction /
    container repair; if None (no ffmpeg on PATH) those steps are skipped
    with a warning (the original bd.ps1 did the same).
    """
    ytdlp = ytdlp or find_ytdlp()
    if not ytdlp:
        ui.error("[错误] 未找到 yt-dlp，请先安装 (pip install -U yt-dlp)")
        return False

    common = _common_args(cookie_path, referer, proxy, insecure)
    tmpl = _template_for(mode, video_dir, audio_dir)
    fmt = _format_for(mode)
    merge = [] if mode == "a" else ["--merge-output-format", "mp4"]

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
        ui.error("[失败] 无法获取视频信息")
        return False
    out_path = Path(predict.stdout.strip())

    # Phase 2: real download (inherit stdout/stderr for the progress bar) ----
    result = subprocess.run([ytdlp, *common, "-f", fmt, *merge, "-o", tmpl, url])

    print()
    if result.returncode != 0 or not out_path.exists():
        ui.error("[失败]")
        return False

    if mode == "a":
        ui.ok(f"[完成!] 音频: {out_path}")
        if ffmpeg_bin:
            ff.repair_audio_container(out_path, ffmpeg_bin)
        else:
            ui.warn("[跳过] ffmpeg 不可用，跳过容器修复")
        return True

    ui.ok(f"[完成!] 视频: {out_path}")
    if mode == "all" and ffmpeg_bin:
        ff.extract_audio(out_path, audio_dir, ffmpeg_bin)
    elif mode == "all":
        ui.warn("[跳过] ffmpeg 不可用，跳过音频提取")
    return True
