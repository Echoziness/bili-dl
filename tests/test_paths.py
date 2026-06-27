"""Tests for cross-platform path resolution.

Uses monkeypatching of ``sys.platform`` and env vars to verify each branch
without running on three OSes. Doesn't touch the real HOME.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bili_dl import paths


def test_config_dir_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert paths.config_dir() == tmp_path / "bili-dl"


def test_config_dir_macos(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(paths.sys, "platform", "darwin")
    monkeypatch.setattr(paths.Path, "home", lambda: tmp_path)
    assert paths.config_dir() == tmp_path / "Library" / "Application Support" / "bili-dl"


def test_config_dir_linux_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(paths.sys, "platform", "linux")
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    assert paths.config_dir() == xdg / "bili-dl"


def test_config_dir_linux_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.setattr(paths.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert paths.config_dir() == tmp_path / ".config" / "bili-dl"


def test_video_dir_per_platform(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(paths.Path, "home", lambda: tmp_path)
    for plat, expected in (
        ("win32", tmp_path / "Videos" / "bilibili_videos"),
        ("darwin", tmp_path / "Movies" / "bilibili_videos"),
    ):
        monkeypatch.setattr(paths.sys, "platform", plat)
        assert paths.default_video_dir() == expected


def test_linux_video_dir_xdg_download(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(paths.sys, "platform", "linux")
    dl = tmp_path / "downloads"
    dl.mkdir()
    monkeypatch.setenv("XDG_DOWNLOAD_DIR", str(dl))
    assert paths.default_video_dir() == dl / "bilibili_videos"


def test_audio_dir_linux_xdg_data(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(paths.sys, "platform", "linux")
    data = tmp_path / "share"
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    assert paths.default_audio_dir() == data / "bili-dl" / "audio"


def test_ensure_dir(tmp_path: Path) -> None:
    p = tmp_path / "a" / "b"
    assert not p.exists()
    result = paths.ensure_dir(p)
    assert p.is_dir()
    assert result == p
    # idempotent
    paths.ensure_dir(p)
