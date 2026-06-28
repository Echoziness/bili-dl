"""Tests for CLI argument parsing.

Guards against regressions in flag threading (mode, proxy, insecure,
cookie/output dirs) without invoking any network or subprocess.
"""

from __future__ import annotations

from pathlib import Path

import pytest

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
