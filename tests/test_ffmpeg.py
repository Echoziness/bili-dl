"""Tests for ffmpeg operations — mock subprocess to test all branches.

No real ffmpeg invoked. Covers:
* repair_audio_container: file missing / success / ffmpeg fails / temp too small / stderr in error
* extract_audio: ffmpeg fails / success (two subprocess calls) / mkdir fails / stderr in error
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bili_dl import ffmpeg as ff


def _completed(args: list[str], rc: int, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args, rc, stdout="", stderr=stderr)


# ─── repair_audio_container ─────────────────────────────────────────────────


def test_repair_file_not_exists(tmp_path: Path) -> None:
    result = ff.repair_audio_container(tmp_path / "nope.m4a", "ffmpeg")
    assert result.success is False
    assert any("不存在" in t for _, t in result.messages)


def test_repair_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    audio = tmp_path / "test.m4a"
    audio.write_bytes(b"\x00" * 1000)

    def fake_run(args: list[str], **kw: object) -> subprocess.CompletedProcess:
        # Output path is the last arg; write 80% → passes 0.5 ratio
        Path(args[-1]).write_bytes(b"\x00" * 800)
        return _completed(args, 0)

    monkeypatch.setattr(ff.subprocess, "run", fake_run)
    result = ff.repair_audio_container(audio, "ffmpeg")
    assert result.success is True
    assert audio.exists()
    # No leftover temp files (name has random suffix, glob for the prefix)
    assert not list(tmp_path.glob("_temp_test_*.m4a"))


def test_repair_ffmpeg_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    audio = tmp_path / "test.m4a"
    audio.write_bytes(b"\x00" * 1000)

    monkeypatch.setattr(
        ff.subprocess, "run", lambda args, **kw: _completed(args, 1, "Codec not found")
    )
    result = ff.repair_audio_container(audio, "ffmpeg")
    assert result.success is False
    assert audio.exists()
    assert any("Codec not found" in t for _, t in result.messages)


def test_repair_temp_too_small(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    audio = tmp_path / "test.m4a"
    audio.write_bytes(b"\x00" * 1000)

    def fake_run(args: list[str], **kw: object) -> subprocess.CompletedProcess:
        Path(args[-1]).write_bytes(b"\x00" * 100)  # 10% → fails 0.5 ratio
        return _completed(args, 0)

    monkeypatch.setattr(ff.subprocess, "run", fake_run)
    result = ff.repair_audio_container(audio, "ffmpeg")
    assert result.success is False
    assert audio.exists()
    assert not list(tmp_path.glob("_temp_test_*.m4a"))


def test_repair_temp_not_created(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    audio = tmp_path / "test.m4a"
    audio.write_bytes(b"\x00" * 1000)
    monkeypatch.setattr(ff.subprocess, "run", lambda args, **kw: _completed(args, 0))
    result = ff.repair_audio_container(audio, "ffmpeg")
    assert result.success is False


# ─── extract_audio ──────────────────────────────────────────────────────────


def test_extract_ffmpeg_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00")
    monkeypatch.setattr(
        ff.subprocess, "run", lambda args, **kw: _completed(args, 1, "No audio stream")
    )
    result = ff.extract_audio(video, tmp_path / "audio", "ffmpeg")
    assert result.success is False
    assert result.audio_path is None
    assert any("No audio stream" in t for _, t in result.messages)


def test_extract_audio_file_not_created(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00")
    monkeypatch.setattr(ff.subprocess, "run", lambda args, **kw: _completed(args, 0))
    result = ff.extract_audio(video, tmp_path / "audio", "ffmpeg")
    assert result.success is False


def test_extract_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00")
    audio_dir = tmp_path / "audio"
    audio_path = audio_dir / "v.m4a"
    calls = [0]

    def fake_run(args: list[str], **kw: object) -> subprocess.CompletedProcess:
        calls[0] += 1
        if calls[0] == 1:
            audio_path.write_bytes(b"\x00" * 1000)
        elif calls[0] == 2:
            # repair's temp output is the last arg (random-suffixed name)
            Path(args[-1]).write_bytes(b"\x00" * 800)
        return _completed(args, 0)

    monkeypatch.setattr(ff.subprocess, "run", fake_run)
    result = ff.extract_audio(video, audio_dir, "ffmpeg")
    assert result.success is True
    assert result.audio_path == audio_path
    assert calls[0] == 2


def test_extract_success_but_repair_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00")
    audio_dir = tmp_path / "audio"
    audio_path = audio_dir / "v.m4a"
    calls = [0]

    def fake_run(args: list[str], **kw: object) -> subprocess.CompletedProcess:
        calls[0] += 1
        if calls[0] == 1:
            audio_path.write_bytes(b"\x00" * 1000)
            return _completed(args, 0)
        return _completed(args, 1, "Repair failed")

    monkeypatch.setattr(ff.subprocess, "run", fake_run)
    result = ff.extract_audio(video, audio_dir, "ffmpeg")
    assert result.success is False


def test_extract_mkdir_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00")
    audio_dir = tmp_path / "audio"

    def fake_mkdir(*a: object, **kw: object) -> None:
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "mkdir", fake_mkdir)
    result = ff.extract_audio(video, audio_dir, "ffmpeg")
    assert result.success is False
    assert any("无法创建" in t for _, t in result.messages)


# ─── find_ffmpeg / _run / _stderr_detail / OSError branches ──────────────────


def test_find_ffmpeg_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ff.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    assert ff.find_ffmpeg() == "/usr/bin/ffmpeg"


def test_find_ffmpeg_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ff.shutil, "which", lambda name: None)
    assert ff.find_ffmpeg() is None


def test_run_returns_rc_and_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ff.subprocess, "run", lambda args, **kw: _completed(args, 2, "err text"))
    rc, stderr = ff._run(["ffmpeg"])
    assert rc == 2
    assert stderr == "err text"


def test_stderr_detail_empty() -> None:
    assert ff._stderr_detail("") == ""


def test_stderr_detail_multiline() -> None:
    """Only the last meaningful line is kept for the error message."""
    assert "last line" in ff._stderr_detail("first\n  last line  \n")


def test_repair_stat_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """OSError reading file sizes → failure, original preserved (§2.20)."""
    audio = tmp_path / "test.m4a"
    audio.write_bytes(b"\x00" * 1000)

    def fake_run(args: list[str], **kw: object) -> subprocess.CompletedProcess:
        Path(args[-1]).write_bytes(b"\x00" * 800)
        return _completed(args, 0)

    monkeypatch.setattr(ff.subprocess, "run", fake_run)

    def bad_stat(self: Path) -> object:
        raise OSError("boom")

    monkeypatch.setattr(Path, "stat", bad_stat)
    result = ff.repair_audio_container(audio, "ffmpeg")
    assert result.success is False
    assert any("无法读取文件大小" in t for _, t in result.messages)
    assert audio.exists()  # original intact


def test_repair_replace_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """OSError replacing (file locked) → failure, temp cleaned, original kept."""
    audio = tmp_path / "test.m4a"
    audio.write_bytes(b"\x00" * 1000)

    def fake_run(args: list[str], **kw: object) -> subprocess.CompletedProcess:
        Path(args[-1]).write_bytes(b"\x00" * 800)
        return _completed(args, 0)

    monkeypatch.setattr(ff.subprocess, "run", fake_run)

    def bad_replace(self: Path, target: Path) -> None:
        raise OSError("locked")

    monkeypatch.setattr(Path, "replace", bad_replace)
    result = ff.repair_audio_container(audio, "ffmpeg")
    assert result.success is False
    assert any("无法替换" in t for _, t in result.messages)
    assert audio.exists()
    assert not list(tmp_path.glob("_temp_test_*.m4a"))  # temp cleaned up
