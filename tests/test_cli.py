"""Tests for CLI argument parsing.

Guards against regressions in flag threading (mode, proxy, insecure,
cookie/output dirs) without invoking any network or subprocess.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bili_dl import cli, cookiestore, downloader
from bili_dl import ffmpeg as ff
from bili_dl.cli import _build_parser


def _parse(*argv: str):
    return _build_parser().parse_args(list(argv))


def test_default_mode_is_none() -> None:
    args = _parse()
    assert args.mode is None
    assert args.url is None


def test_video_mode() -> None:
    args = _parse("-v", "https://bilibili.com/video/BV123")
    assert args.mode == "v"


def test_audio_mode() -> None:
    args = _parse("-a", "https://bilibili.com/video/BV123")
    assert args.mode == "a"


def test_all_mode() -> None:
    args = _parse("--all", "https://bilibili.com/video/BV123")
    assert args.mode == "all"


def test_mode_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        _parse("-v", "-a", "https://bilibili.com/video/BV123")


def test_proxy() -> None:
    args = _parse("--proxy", "http://127.0.0.1:7890", "https://bilibili.com/video/BV123")
    assert args.proxy == "http://127.0.0.1:7890"


def test_insecure_short() -> None:
    args = _parse("-k", "https://bilibili.com/video/BV123")
    assert args.insecure is True


def test_insecure_long() -> None:
    args = _parse("--insecure", "https://bilibili.com/video/BV123")
    assert args.insecure is True


def test_cookie_dir() -> None:
    args = _parse("--cookie-dir", "/tmp/cookies", "https://bilibili.com/video/BV123")
    assert args.cookie_dir == Path("/tmp/cookies")


def test_output_dir() -> None:
    args = _parse("--output-dir", "/tmp/videos", "https://bilibili.com/video/BV123")
    assert args.output_dir == Path("/tmp/videos")


def test_audio_dir() -> None:
    args = _parse("--audio-dir", "/tmp/audio", "https://bilibili.com/video/BV123")
    assert args.audio_dir == Path("/tmp/audio")


def test_url_positional() -> None:
    url = "https://www.bilibili.com/video/BV1xx411c7mD"
    args = _parse(url)
    assert args.url == url


def test_no_url_repl_mode() -> None:
    args = _parse()
    assert args.url is None


# ─── main() with mocked dependencies ────────────────────────────────────────


def test_main_no_ytdlp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(downloader, "find_ytdlp", lambda: None)
    assert cli.main([]) == 1


def test_main_no_ffmpeg_warns(monkeypatch: pytest.MonkeyPatch, capsys: pytest.Capture) -> None:
    monkeypatch.setattr(downloader, "find_ytdlp", lambda: "yt-dlp")
    monkeypatch.setattr(ff, "find_ffmpeg", lambda: None)
    # Cookie fails → return 1 before reaching REPL
    monkeypatch.setattr(cli, "_prepare_cookie", lambda opts: False)
    assert cli.main([]) == 1
    captured = capsys.readouterr()
    assert "ffmpeg" in captured.out


def test_main_cookie_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(downloader, "find_ytdlp", lambda: "yt-dlp")
    monkeypatch.setattr(ff, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(cli, "_prepare_cookie", lambda opts: False)
    assert cli.main([]) == 1


def test_main_non_interactive_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.Capture
) -> None:
    monkeypatch.setattr(downloader, "find_ytdlp", lambda: "yt-dlp")
    monkeypatch.setattr(ff, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(cli, "_prepare_cookie", lambda opts: True)
    monkeypatch.setattr(cli, "_run_once", lambda opts, url, ytdlp, ffmpeg_bin: True)
    result = cli.main(["https://bilibili.com/video/BV1"])
    assert result == 0


def test_main_non_interactive_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.Capture
) -> None:
    monkeypatch.setattr(downloader, "find_ytdlp", lambda: "yt-dlp")
    monkeypatch.setattr(ff, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(cli, "_prepare_cookie", lambda opts: True)
    monkeypatch.setattr(cli, "_run_once", lambda opts, url, ytdlp, ffmpeg_bin: False)
    result = cli.main(["https://bilibili.com/video/BV1"])
    assert result == 1


def test_main_repl_eof_exits_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """REPL should exit 0 on EOF (stdin closed / redirected)."""
    monkeypatch.setattr(downloader, "find_ytdlp", lambda: "yt-dlp")
    monkeypatch.setattr(ff, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(cli, "_prepare_cookie", lambda opts: True)

    def raise_eof(prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("bili_dl.ui.prompt", raise_eof)
    assert cli.main([]) == 0


def test_prepare_cookie_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cookiestore,
        "ensure_cookie",
        lambda cookie_dir: cookiestore.EnsureResult(ready=True, messages=[("ok", "ok")]),
    )
    opts = cli.Options()
    assert cli._prepare_cookie(opts) is True


def test_prepare_cookie_failure(monkeypatch: pytest.MonkeyPatch, capsys: pytest.Capture) -> None:
    monkeypatch.setattr(
        cookiestore,
        "ensure_cookie",
        lambda cookie_dir: cookiestore.EnsureResult(ready=False, messages=[("error", "nope")]),
    )
    opts = cli.Options()
    assert cli._prepare_cookie(opts) is False
    captured = capsys.readouterr()
    assert "没有可用的 B 站 Cookie" in captured.out


def test_emit_dispatches_to_ui(monkeypatch: pytest.MonkeyPatch, capsys: pytest.Capture) -> None:
    cli._emit([("info", "hello"), ("error", "boom")])
    captured = capsys.readouterr()
    assert "hello" in captured.out
    assert "boom" in captured.out
