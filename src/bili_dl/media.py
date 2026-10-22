"""yt-dlp invocation and download orchestration.

The original bd.ps1 had a scoping bug: ``$commonOpt`` was defined in the main
flow but referenced inside ``Invoke-DownloadVideo`` as ``@commonOpt``, which
PowerShell functions *can't* see by default — so the cookie/referer args
were silently dropped and 1080p downloads quietly degraded. Here the common
options are built from a :class:`DownloadConfig` dataclass threaded into the
call, so the bug class cannot recur.

Two-phase download (unchanged from bd.ps1):
  1. ``--print filename --skip-download`` to predict the final output path
     (without writing anything) so we can report it and locate the file.
     This machine-readable channel is explicitly UTF-8 so characters outside
     the Windows system code page still match the Unicode filename on disk.
  2. The real download with the same template/format, stdout inherited so
     yt-dlp's progress bar streams straight to the terminal.

This module is pure logic — it returns :class:`DownloadResult` objects and
never calls ``ui.*`` directly. The controller (``cli.py``) is responsible
for turning result messages into terminal output.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from . import ffmpeg as ff
from .config import FMT_AUDIO, FMT_AV
from .models import DownloadConfig, DownloadResult


def find_ytdlp() -> Optional[tuple[str, ...]]:
    """Return the dependency's command in this Python environment."""
    if importlib.util.find_spec("yt_dlp") is None:
        return None
    return (sys.executable, "-m", "yt_dlp")


def _template_for(mode: str, video_dir: Path, audio_dir: Path) -> str:
    if mode == "a":
        return str(audio_dir / "%(title)s.%(ext)s")
    return str(video_dir / "%(title)s.%(ext)s")


def _format_for(mode: str) -> str:
    return FMT_AUDIO if mode == "a" else FMT_AV


def _common_args(cfg: DownloadConfig) -> list[str]:
    """Build the yt-dlp args shared by phase 1 (predict) and phase 2 (download).

    ``--proxy`` is always present because yt-dlp defines
    an empty value as direct connection; omitting it would let the subprocess
    re-read inherited proxy environment variables after the CLI resolved an
    explicit ``proxy = ""``.
    """
    args: list[str] = [
        "--ignore-config",
        "--no-playlist",
        "--retries",
        "10",
        "--cookies",
        str(cfg.cookie_path),
    ]
    args += ["--add-header", f"Referer:{cfg.referer}"]
    args += ["--proxy", cfg.proxy]
    # Even an unavailable explicit override must reach yt-dlp; omitting it
    # would let that child silently discover a different system binary.
    ffmpeg_location = cfg.ffmpeg_bin or os.environ.get("IMAGEIO_FFMPEG_EXE")
    if ffmpeg_location:
        args += ["--ffmpeg-location", ffmpeg_location]
    if cfg.insecure:
        args.append("--no-check-certificate")
    return args


def download(url: str, cfg: DownloadConfig) -> DownloadResult:
    """Download one URL in the given mode; post-process as needed.

    Returns a :class:`DownloadResult`. ``cfg.ffmpeg_bin`` controls audio
    extraction / container repair; if ``None`` (no usable ffmpeg) those
    steps are skipped with a warning message.
    """
    ytdlp = cfg.ytdlp or find_ytdlp()
    if not ytdlp:
        return DownloadResult(
            success=False,
            messages=[("error", "[错误] bili-dl 的 yt-dlp 依赖缺失，请重新安装 bili-dl")],
        )

    common = _common_args(cfg)
    tmpl = _template_for(cfg.mode, cfg.video_dir, cfg.audio_dir)
    fmt = _format_for(cfg.mode)
    merge = [] if cfg.mode == "a" else ["--merge-output-format", "mp4"]
    # The child uses this environment's Python and resolves its own stdlib.
    ytdlp_env = os.environ.copy()
    ytdlp_env.pop("PYTHONHOME", None)

    # Phase 1: predict output path (no download) -------------------------------
    if cfg.stage:
        cfg.stage("prepare", None)
    predict = subprocess.run(
        [
            *ytdlp,
            *common,
            "--encoding",
            "utf-8",
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
        encoding="utf-8",
        env=ytdlp_env,
    )
    if not predict.stdout.strip():
        detail = predict.stderr.strip().splitlines()
        hint = detail[-1] if detail else "yt-dlp 未能获取视频信息"
        return DownloadResult(
            success=False,
            messages=[("error", f"[失败] 无法获取视频信息: {hint}")],
        )
    paths = predict.stdout.strip().splitlines()
    if len(paths) != 1:
        return DownloadResult(
            success=False,
            messages=[
                ("error", "[失败] 此链接包含多个输出目标；暂不支持旧式分段视频，请提供单个视频链接")
            ],
        )
    out_path = Path(paths[0])

    # Phase 1 warnings (yt-dlp exit code is not a reliable failure
    # signal; the path is available, as in Phase 2's file-exists check).
    predict_warn: list[tuple[str, str]] = []
    if predict.returncode != 0:
        predict_warn.append(
            ("warn", f"[警告] yt-dlp 退出码 {predict.returncode}（predict 非零，但路径已获取）")
        )

    # Phase 2: real download (inherit stdout/stderr for the progress bar) ----
    presentation: list[str] = []
    if cfg.stage:
        # The caller stops its live display before handing over the terminal.
        cfg.stage("download", out_path)
        presentation = ["--quiet", "--progress"]
        if cfg.no_color:
            presentation += ["--color", "never"]
    result = subprocess.run(
        [*ytdlp, *common, *presentation, "-f", fmt, *merge, "-o", tmpl, url], env=ytdlp_env
    )

    if not out_path.exists():
        return DownloadResult(
            success=False,
            messages=[("error", "[失败] 下载未完成 — 请查看上方 yt-dlp 输出获取详细错误信息")],
        )

    # Post-processing ---------------------------------------------------------
    if cfg.mode == "a":
        msgs: list[tuple[str, str]] = list(predict_warn)
        msgs.append(("ok", f"[完成!] 音频: {out_path}"))
        if result.returncode != 0:
            msgs.append(("warn", f"[警告] yt-dlp 退出码 {result.returncode}（非零，但文件已生成）"))
        if cfg.ffmpeg_bin:
            if cfg.stage:
                cfg.stage("repair", out_path)
            repair = ff.repair_audio_container(out_path, cfg.ffmpeg_bin)
            msgs.extend(repair.messages)
        else:
            msgs.append(("warn", "[跳过] ffmpeg 不可用，跳过容器修复"))
        return DownloadResult(success=True, messages=msgs, output_path=out_path)

    # mode == "all" or "v" — video path
    video_msgs: list[tuple[str, str]] = list(predict_warn)
    video_msgs.append(("ok", f"[完成!] 视频: {out_path}"))
    if result.returncode != 0:
        video_msgs.append(
            ("warn", f"[警告] yt-dlp 退出码 {result.returncode}（非零，但文件已生成）")
        )
    additional: list[Path] = []
    if cfg.mode == "all" and cfg.ffmpeg_bin:
        if cfg.stage:
            cfg.stage("extract", out_path)
        extract = ff.extract_audio(out_path, cfg.audio_dir, cfg.ffmpeg_bin)
        video_msgs.extend(extract.messages)
        if extract.audio_path is not None:
            additional.append(extract.audio_path)
    elif cfg.mode == "all":
        video_msgs.append(("warn", "[跳过] ffmpeg 不可用，跳过音频提取"))
    return DownloadResult(
        success=True, messages=video_msgs, output_path=out_path, additional_paths=additional
    )
