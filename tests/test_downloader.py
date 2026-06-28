"""Tests for yt-dlp argument assembly and template/format selection.

Pure-logic functions with no side effects — no subprocess, no network.
Guards against regressions in the cookie/referer/proxy/insecure flag
threading that the original bd.ps1 scoping bug was about (AGENTS.md §2.1).
"""

from __future__ import annotations

from pathlib import Path

from bili_dl import downloader
from bili_dl.config import FMT_AUDIO, FMT_AV, REFERER
from bili_dl.downloader import DownloadConfig


def _cfg(
    tmp_path: Path,
    proxy: str = "",
    insecure: bool = False,
) -> DownloadConfig:
    return DownloadConfig(
        mode="all",
        video_dir=tmp_path / "videos",
        audio_dir=tmp_path / "audio",
        cookie_path=tmp_path / "cookies.txt",
        proxy=proxy,
        insecure=insecure,
    )


def test_common_args_basic(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    args = downloader._common_args(cfg)
    assert "--no-playlist" in args
    idx = args.index("--cookies")
    assert args[idx + 1] == str(cfg.cookie_path)
    idx = args.index("--add-header")
    assert args[idx + 1] == f"Referer:{REFERER}"
    assert "--proxy" not in args
    assert "--no-check-certificate" not in args


def test_common_args_proxy(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, proxy="http://127.0.0.1:7890")
    args = downloader._common_args(cfg)
    idx = args.index("--proxy")
    assert args[idx + 1] == "http://127.0.0.1:7890"


def test_common_args_insecure(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, insecure=True)
    args = downloader._common_args(cfg)
    assert "--no-check-certificate" in args


def test_common_args_no_proxy_no_insecure(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    args = downloader._common_args(cfg)
    assert "--proxy" not in args
    assert "--no-check-certificate" not in args


def test_template_for_audio(tmp_path: Path) -> None:
    video_dir = tmp_path / "videos"
    audio_dir = tmp_path / "audio"
    tmpl = downloader._template_for("a", video_dir, audio_dir)
    assert tmpl == str(audio_dir / "%(title)s.%(ext)s")
    assert str(video_dir) not in tmpl


def test_template_for_video_modes(tmp_path: Path) -> None:
    video_dir = tmp_path / "videos"
    audio_dir = tmp_path / "audio"
    for mode in ("all", "v"):
        tmpl = downloader._template_for(mode, video_dir, audio_dir)
        assert tmpl == str(video_dir / "%(title)s.%(ext)s")


def test_format_for_audio() -> None:
    assert downloader._format_for("a") == FMT_AUDIO


def test_format_for_video_modes() -> None:
    for mode in ("all", "v"):
        assert downloader._format_for(mode) == FMT_AV


def test_download_config_defaults() -> None:
    cfg = DownloadConfig(
        mode="all",
        video_dir=Path("/tmp/v"),
        audio_dir=Path("/tmp/a"),
        cookie_path=Path("/tmp/c.txt"),
    )
    assert cfg.proxy == ""
    assert cfg.insecure is False
    assert cfg.ytdlp is None
    assert cfg.ffmpeg_bin is None
    assert cfg.referer == REFERER
