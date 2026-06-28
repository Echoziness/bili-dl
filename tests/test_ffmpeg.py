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
    temp = tmp_path / "_temp_test.m4a"

    def fake_run(args: list[str], **kw: object) -> subprocess.CompletedProcess:
        temp.write_bytes(b"\x00" * 800)  # 80% → passes 0.5 ratio
        return _completed(args, 0)

    monkeypatch.setattr(ff.subprocess, "run", fake_run)
    result = ff.repair_audio_container(audio, "ffmpeg")
    assert result.success is True
    assert audio.exists()
    assert not temp.exists()


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
    temp = tmp_path / "_temp_test.m4a"

    def fake_run(args: list[str], **kw: object) -> subprocess.CompletedProcess:
        temp.write_bytes(b"\x00" * 100)  # 10% → fails 0.5 ratio
        return _completed(args, 0)

    monkeypatch.setattr(ff.subprocess, "run", fake_run)
    result = ff.repair_audio_container(audio, "ffmpeg")
    assert result.success is False
    assert audio.exists()
    assert not temp.exists()


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
    temp_path = audio_dir / "_temp_v.m4a"
    calls = [0]

    def fake_run(args: list[str], **kw: object) -> subprocess.CompletedProcess:
        calls[0] += 1
        if calls[0] == 1:
            audio_path.write_bytes(b"\x00" * 1000)
        elif calls[0] == 2:
            temp_path.write_bytes(b"\x00" * 800)
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
