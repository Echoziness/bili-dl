"""Tests for CLI argument parsing and main() flow.

Guards against regressions in flag threading and dependency checking
without invoking any network or subprocess.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bili_dl import cli, downloader
from bili_dl import ffmpeg as ff
from bili_dl.cli import _build_parser


def _parse(*argv: str):
    return _build_parser().parse_args(list(argv))


def test_default_mode_is_none() -> None:
    args = _parse()
    assert args.mode is None
    assert args.url is None


def test_video_mode() -> None:
    assert _parse("-v", "https://bilibili.com/video/BV123").mode == "v"


def test_audio_mode() -> None:
    assert _parse("-a", "https://bilibili.com/video/BV123").mode == "a"


def test_all_mode() -> None:
    assert _parse("--all", "https://bilibili.com/video/BV123").mode == "all"


def test_mode_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        _parse("-v", "-a", "https://bilibili.com/video/BV123")


def test_proxy() -> None:
    args = _parse("--proxy", "http://127.0.0.1:7890", "https://bilibili.com/video/BV123")
    assert args.proxy == "http://127.0.0.1:7890"


def test_insecure() -> None:
    assert _parse("-k", "https://bilibili.com/video/BV123").insecure is True


def test_cookie_dir() -> None:
    assert _parse("--cookie-dir", "/tmp/cookies", "https://x").cookie_dir == Path("/tmp/cookies")


def test_output_dir() -> None:
    assert _parse("--output-dir", "/tmp/videos", "https://x").output_dir == Path("/tmp/videos")


def test_audio_dir() -> None:
    assert _parse("--audio-dir", "/tmp/audio", "https://x").audio_dir == Path("/tmp/audio")


def test_url_positional() -> None:
    url = "https://www.bilibili.com/video/BV1xx411c7mD"
    assert _parse(url).url == url


# ─── main() with mocked dependencies ────────────────────────────────────────


def test_main_no_ytdlp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(downloader, "find_ytdlp", lambda: None)
    assert cli.main([]) == 1


def test_main_no_ffmpeg_warns(monkeypatch: pytest.MonkeyPatch, capsys: pytest.Capture) -> None:
    monkeypatch.setattr(downloader, "find_ytdlp", lambda: "yt-dlp")
    monkeypatch.setattr(ff, "find_ffmpeg", lambda: None)
    monkeypatch.setattr(cli, "_prepare_cookie", lambda opts: False)
    assert cli.main([]) == 1
    assert "ffmpeg" in capsys.readouterr().out


def test_main_non_interactive_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(downloader, "find_ytdlp", lambda: "yt-dlp")
    monkeypatch.setattr(ff, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(cli, "_prepare_cookie", lambda opts: True)
    monkeypatch.setattr(cli, "_run_once", lambda opts, url, ytdlp, ffmpeg_bin: True)
    assert cli.main(["https://bilibili.com/video/BV1"]) == 0


def test_main_non_interactive_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(downloader, "find_ytdlp", lambda: "yt-dlp")
    monkeypatch.setattr(ff, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(cli, "_prepare_cookie", lambda opts: True)
    monkeypatch.setattr(cli, "_run_once", lambda opts, url, ytdlp, ffmpeg_bin: False)
    assert cli.main(["https://bilibili.com/video/BV1"]) == 1


def test_main_repl_eof_exits_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """REPL should exit 0 on EOF (stdin closed / redirected)."""
    monkeypatch.setattr(downloader, "find_ytdlp", lambda: "yt-dlp")
    monkeypatch.setattr(ff, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(cli, "_prepare_cookie", lambda opts: True)

    def raise_eof(prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("bili_dl.ui.prompt", raise_eof)
    assert cli.main([]) == 0
