"""Tests for yt-dlp argument assembly and download orchestration.

Part 1 — argument threading (the project's reason for existing, §2.1).

Part 2 — download() with mocked subprocess: Phase 1 predict failure,
Phase 2 failure, success paths for all three modes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bili_dl import downloader
from bili_dl.config import REFERER
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


# ─── Part 2: download() with mocked subprocess ──────────────────────────────


def _completed_process(
    args: list[str], returncode: int, stdout: str = ""
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="")


def test_download_no_ytdlp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(downloader, "find_ytdlp", lambda: None)
    cfg = _cfg(tmp_path)
    result = downloader.download("https://bilibili.com/video/BV1", cfg)
    assert result.success is False
    assert any("yt-dlp" in t for _, t in result.messages)


def test_download_phase1_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(downloader, "find_ytdlp", lambda: "yt-dlp")

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        return _completed_process(args, 1)

    monkeypatch.setattr(downloader.subprocess, "run", fake_run)
    cfg = DownloadConfig(
        mode="all",
        video_dir=tmp_path / "v",
        audio_dir=tmp_path / "a",
        cookie_path=tmp_path / "c.txt",
        ytdlp="yt-dlp",
    )
    result = downloader.download("https://bilibili.com/video/BV1", cfg)
    assert result.success is False


def test_download_phase2_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(downloader, "find_ytdlp", lambda: "yt-dlp")
    out_file = tmp_path / "v" / "title.mp4"
    calls = [0]

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls[0] += 1
        if calls[0] == 1:  # phase 1 predict
            return _completed_process(args, 0, stdout=str(out_file))
        # phase 2: return failure, don't create file
        return _completed_process(args, 1)

    monkeypatch.setattr(downloader.subprocess, "run", fake_run)
    cfg = DownloadConfig(
        mode="v",
        video_dir=tmp_path / "v",
        audio_dir=tmp_path / "a",
        cookie_path=tmp_path / "c.txt",
        ytdlp="yt-dlp",
        ffmpeg_bin=None,
    )
    result = downloader.download("https://bilibili.com/video/BV1", cfg)
    assert result.success is False


def test_download_video_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(downloader, "find_ytdlp", lambda: "yt-dlp")
    out_file = tmp_path / "v" / "title.mp4"
    calls = [0]

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls[0] += 1
        if calls[0] == 1:  # phase 1 predict
            return _completed_process(args, 0, stdout=str(out_file))
        # phase 2: create the file
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_bytes(b"\x00")
        return _completed_process(args, 0)

    monkeypatch.setattr(downloader.subprocess, "run", fake_run)
    cfg = DownloadConfig(
        mode="v",
        video_dir=tmp_path / "v",
        audio_dir=tmp_path / "a",
        cookie_path=tmp_path / "c.txt",
        ytdlp="yt-dlp",
        ffmpeg_bin=None,
    )
    result = downloader.download("https://bilibili.com/video/BV1", cfg)
    assert result.success is True
    assert result.output_path == out_file


def test_download_audio_no_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(downloader, "find_ytdlp", lambda: "yt-dlp")
    out_file = tmp_path / "a" / "title.m4a"
    calls = [0]

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls[0] += 1
        if calls[0] == 1:
            return _completed_process(args, 0, stdout=str(out_file))
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_bytes(b"\x00")
        return _completed_process(args, 0)

    monkeypatch.setattr(downloader.subprocess, "run", fake_run)
    cfg = DownloadConfig(
        mode="a",
        video_dir=tmp_path / "v",
        audio_dir=tmp_path / "a",
        cookie_path=tmp_path / "c.txt",
        ytdlp="yt-dlp",
        ffmpeg_bin=None,
    )
    result = downloader.download("https://bilibili.com/video/BV1", cfg)
    assert result.success is True
    # Should have a "skip" warning for no ffmpeg
    assert any("跳过" in t for _, t in result.messages)


def test_download_all_no_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(downloader, "find_ytdlp", lambda: "yt-dlp")
    out_file = tmp_path / "v" / "title.mp4"
    calls = [0]

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls[0] += 1
        if calls[0] == 1:
            return _completed_process(args, 0, stdout=str(out_file))
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_bytes(b"\x00")
        return _completed_process(args, 0)

    monkeypatch.setattr(downloader.subprocess, "run", fake_run)
    cfg = DownloadConfig(
        mode="all",
        video_dir=tmp_path / "v",
        audio_dir=tmp_path / "a",
        cookie_path=tmp_path / "c.txt",
        ytdlp="yt-dlp",
        ffmpeg_bin=None,
    )
    result = downloader.download("https://bilibili.com/video/BV1", cfg)
    assert result.success is True
    # Should have a "skip" warning for audio extraction
    assert any("跳过" in t for _, t in result.messages)


def test_download_all_with_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(downloader, "find_ytdlp", lambda: "yt-dlp")
    out_file = tmp_path / "v" / "title.mp4"
    calls = [0]

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls[0] += 1
        if calls[0] == 1:
            return _completed_process(args, 0, stdout=str(out_file))
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_bytes(b"\x00")
        return _completed_process(args, 0)

    monkeypatch.setattr(downloader.subprocess, "run", fake_run)

    # Mock ffmpeg.extract_audio to return success
    from bili_dl import ffmpeg as ff

    def fake_extract(video: Path, audio_dir: Path, ffmpeg_bin: str) -> ff.ExtractResult:
        return ff.ExtractResult(
            success=True,
            messages=[("info", "[提取音频] title"), ("ok", "[完成!] 容器已修复")],
            audio_path=audio_dir / "title.m4a",
        )

    monkeypatch.setattr(ff, "extract_audio", fake_extract)

    cfg = DownloadConfig(
        mode="all",
        video_dir=tmp_path / "v",
        audio_dir=tmp_path / "a",
        cookie_path=tmp_path / "c.txt",
        ytdlp="yt-dlp",
        ffmpeg_bin="ffmpeg",
    )
    result = downloader.download("https://bilibili.com/video/BV1", cfg)
    assert result.success is True
    assert any("提取音频" in t for _, t in result.messages)
