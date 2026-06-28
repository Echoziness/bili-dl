"""Tests for ffmpeg operations — mock subprocess to test all branches.

No real ffmpeg invoked. Covers:
* repair_audio_container: file missing / success / ffmpeg fails / temp too small
* extract_audio: ffmpeg fails / success (two subprocess calls)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bili_dl import ffmpeg as ff

# ─── repair_audio_container ─────────────────────────────────────────────────


def test_repair_file_not_exists(tmp_path: Path) -> None:
    result = ff.repair_audio_container(tmp_path / "nope.m4a", "ffmpeg")
    assert result.success is False
    assert any("不存在" in t for _, t in result.messages)


def test_repair_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    audio = tmp_path / "test.m4a"
    audio.write_bytes(b"\x00" * 1000)
    temp = tmp_path / "_temp_test.m4a"

    def fake_call(args: list[str]) -> int:
        temp.write_bytes(b"\x00" * 800)  # 80% → passes 0.5 ratio
        return 0

    monkeypatch.setattr(ff.subprocess, "call", fake_call)
    result = ff.repair_audio_container(audio, "ffmpeg")
    assert result.success is True
    assert audio.exists()  # temp replaced original
    assert not temp.exists()  # temp was moved


def test_repair_ffmpeg_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    audio = tmp_path / "test.m4a"
    audio.write_bytes(b"\x00" * 1000)

    monkeypatch.setattr(ff.subprocess, "call", lambda args: 1)
    result = ff.repair_audio_container(audio, "ffmpeg")
    assert result.success is False
    assert audio.exists()  # original preserved


def test_repair_temp_too_small(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    audio = tmp_path / "test.m4a"
    audio.write_bytes(b"\x00" * 1000)
    temp = tmp_path / "_temp_test.m4a"

    def fake_call(args: list[str]) -> int:
        temp.write_bytes(b"\x00" * 100)  # 10% → fails 0.5 ratio
        return 0

    monkeypatch.setattr(ff.subprocess, "call", fake_call)
    result = ff.repair_audio_container(audio, "ffmpeg")
    assert result.success is False
    assert audio.exists()  # original preserved
    assert not temp.exists()  # temp cleaned up


def test_repair_temp_not_created(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    audio = tmp_path / "test.m4a"
    audio.write_bytes(b"\x00" * 1000)

    # ffmpeg returns 0 but doesn't write the temp file
    monkeypatch.setattr(ff.subprocess, "call", lambda args: 0)
    result = ff.repair_audio_container(audio, "ffmpeg")
    assert result.success is False


# ─── extract_audio ──────────────────────────────────────────────────────────


def test_extract_ffmpeg_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00")
    monkeypatch.setattr(ff.subprocess, "call", lambda args: 1)
    result = ff.extract_audio(video, tmp_path / "audio", "ffmpeg")
    assert result.success is False
    assert result.audio_path is None


def test_extract_audio_file_not_created(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00")
    # ffmpeg returns 0 but doesn't write the output file
    monkeypatch.setattr(ff.subprocess, "call", lambda args: 0)
    result = ff.extract_audio(video, tmp_path / "audio", "ffmpeg")
    assert result.success is False


def test_extract_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00")
    audio_dir = tmp_path / "audio"
    audio_path = audio_dir / "v.m4a"
    temp_path = audio_dir / "_temp_v.m4a"

    call_count = [0]

    def fake_call(args: list[str]) -> int:
        call_count[0] += 1
        if call_count[0] == 1:
            # extract phase: write the audio file
            audio_path.write_bytes(b"\x00" * 1000)
        elif call_count[0] == 2:
            # repair phase: write temp file
            temp_path.write_bytes(b"\x00" * 800)
        return 0

    monkeypatch.setattr(ff.subprocess, "call", fake_call)
    result = ff.extract_audio(video, audio_dir, "ffmpeg")
    assert result.success is True
    assert result.audio_path == audio_path
    assert call_count[0] == 2  # extract + repair


def test_extract_success_but_repair_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00")
    audio_dir = tmp_path / "audio"
    audio_path = audio_dir / "v.m4a"

    call_count = [0]

    def fake_call(args: list[str]) -> int:
        call_count[0] += 1
        if call_count[0] == 1:
            audio_path.write_bytes(b"\x00" * 1000)
            return 0
        # repair phase: fail
        return 1

    monkeypatch.setattr(ff.subprocess, "call", fake_call)
    result = ff.extract_audio(video, audio_dir, "ffmpeg")
    assert result.success is False
