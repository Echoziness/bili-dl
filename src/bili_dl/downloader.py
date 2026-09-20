"""Unified download entry point: prepare output directories and dispatch by mode."""

from __future__ import annotations

from . import media, subtitles
from .config import VALID_MODES
from .media import find_ytdlp as find_ytdlp
from .models import DownloadConfig as DownloadConfig
from .models import DownloadResult as DownloadResult
from .paths import ensure_dir


def download(url: str, cfg: DownloadConfig) -> DownloadResult:
    """Run one download without creating unrelated media directories."""
    if cfg.mode not in VALID_MODES:
        return DownloadResult(False, [("error", "[错误] 不支持的下载模式")])
    directories = [cfg.audio_dir] if cfg.mode == "a" else [cfg.video_dir]
    if cfg.mode == "all":
        directories.append(cfg.audio_dir)
    try:
        for directory in directories:
            ensure_dir(directory)
    except OSError as exc:
        return DownloadResult(False, [("error", f"[错误] 无法创建输出目录: {exc}")])
    if cfg.mode == "s":
        return subtitles.download(url, cfg)
    return media.download(url, cfg)
