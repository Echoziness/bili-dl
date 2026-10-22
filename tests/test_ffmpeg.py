"""Binary discovery and error handling for zero-copy ffmpeg operations.

Remux failure branches use mocked subprocesses. Covers:
* repair_audio_container: file missing / success / ffmpeg fails / temp too small / stderr in error
* extract_audio: ffmpeg fails / success (two subprocess calls) / mkdir fails / stderr in error
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg
import pytest

from bili_dl import ffmpeg as ff


def test_resolve_bundled_binary_without_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IMAGEIO_FFMPEG_EXE", raising=False)
    monkeypatch.setenv("PATH", "")
    executable = ff.find_ffmpeg()
    assert executable is not None and Path(executable).is_file()
    assert Path(executable).parent == Path(imageio_ffmpeg.__file__).parent / "binaries"


def test_external_ffmpeg_fallback_is_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = str(tmp_path / "ffmpeg")
    monkeypatch.delenv("IMAGEIO_FFMPEG_EXE", raising=False)
    monkeypatch.setattr(imageio_ffmpeg, "get_ffmpeg_exe", lambda: "ffmpeg")
    monkeypatch.setattr(ff.shutil, "which", lambda name: executable)
    assert ff.find_ffmpeg() == executable


def test_no_binary_is_a_supported_degradation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IMAGEIO_FFMPEG_EXE", raising=False)

    def unavailable() -> str:
        raise RuntimeError("no binary for this platform")

    monkeypatch.setattr(imageio_ffmpeg, "get_ffmpeg_exe", unavailable)
    assert ff.find_ffmpeg() is None


@pytest.mark.parametrize(
    "outcome", [0, 1, OSError("cannot execute"), subprocess.TimeoutExpired("ffmpeg", 5)]
)
def test_explicit_ffmpeg_override_is_validated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: object
) -> None:
    executable = str(tmp_path / "ffmpeg.exe")
    monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", executable)
    monkeypatch.setattr(ff.shutil, "which", lambda name: executable)
    calls = []

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls.append((args, kwargs))
        if isinstance(outcome, BaseException):
            raise outcome
        return subprocess.CompletedProcess(args, outcome)

    monkeypatch.setattr(ff.subprocess, "run", run)
    assert ff.find_ffmpeg() == (executable if outcome == 0 else None)
    assert calls == [([executable, "-version"], {"capture_output": True, "timeout": 5})]


def test_missing_override_does_not_silently_select_another_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", "missing-ffmpeg")
    monkeypatch.setattr(ff.shutil, "which", lambda name: None)
    monkeypatch.setattr(imageio_ffmpeg, "get_ffmpeg_exe", lambda: pytest.fail("Explicit choice"))
    assert ff.find_ffmpeg() is None


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


def test_run_returns_rc_and_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ff.subprocess, "run", lambda args, **kw: _completed(args, 2, "err text"))
    rc, stderr = ff._run(["ffmpeg"])
    assert rc == 2
    assert stderr == "err text"


@pytest.mark.parametrize("payload", ["中文错误 🌟".encode(), b"\xffError opening input"])
def test_captured_diagnostics_handle_unicode_and_invalid_bytes(payload: bytes) -> None:
    command = [
        sys.executable,
        "-c",
        f"import sys; sys.stderr.buffer.write({payload!r}); sys.exit(1)",
    ]
    rc, stderr = ff._run(command)
    assert rc == 1 and stderr
    if payload.startswith(b"\xff"):
        assert "Error opening input" in stderr
    else:
        assert stderr == "中文错误 🌟"


def test_stderr_detail_empty() -> None:
    assert ff._stderr_detail("") == ""


def test_stderr_detail_multiline() -> None:
    """Only the last meaningful line is kept for the error message."""
    assert "last line" in ff._stderr_detail("first\n  last line  \n")


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


# ─── CFA diagnosis (v0.3.0) ───────────────────────────────────────────────────


def test_cfa_enabled_false_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ff.sys, "platform", "linux")
    assert ff._cfa_enabled() is False


def test_cfa_enabled_registry_returns_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ff.sys, "platform", "win32")

    class _FakeKey:
        def __enter__(self) -> _FakeKey:
            return self

        def __exit__(self, *a: object) -> None:
            return None

    class _FakeWinreg:
        HKEY_LOCAL_MACHINE = 0x80000002

        def OpenKey(self, *a: object, **kw: object) -> _FakeKey:
            return _FakeKey()

        def QueryValueEx(self, *a: object) -> tuple[int, int]:
            return (1, 4)

        def CloseKey(self, *a: object) -> None:
            return None

    import sys

    monkeypatch.setitem(sys.modules, "winreg", _FakeWinreg())
    assert ff._cfa_enabled() is True


def test_cfa_enabled_registry_returns_0(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ff.sys, "platform", "win32")

    class _FakeKey:
        def __enter__(self) -> _FakeKey:
            return self

        def __exit__(self, *a: object) -> None:
            return None

    class _FakeWinreg:
        HKEY_LOCAL_MACHINE = 0x80000002

        def OpenKey(self, *a: object, **kw: object) -> _FakeKey:
            return _FakeKey()

        def QueryValueEx(self, *a: object) -> tuple[int, int]:
            return (0, 4)

        def CloseKey(self, *a: object) -> None:
            return None

    import sys

    monkeypatch.setitem(sys.modules, "winreg", _FakeWinreg())
    assert ff._cfa_enabled() is False


def test_cfa_hint_only_when_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hint fires only when dir exists + writable + CFA on."""
    monkeypatch.setattr(ff.sys, "platform", "win32")
    target = tmp_path / "out.m4a"

    # CFA off → no hint
    monkeypatch.setattr(ff, "_cfa_enabled", lambda: False)
    assert ff._cfa_hint(target) is None

    # CFA on → hint fires
    monkeypatch.setattr(ff, "_cfa_enabled", lambda: True)
    hint = ff._cfa_hint(target)
    assert hint is not None
    assert "受控文件夹访问" in hint

    # Parent missing → no hint (file not created for a different reason)
    monkeypatch.setattr(ff.os, "access", lambda *a, **kw: True)
    assert ff._cfa_hint(tmp_path / "missing" / "out.m4a") is None


def test_cfa_hint_non_windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ff.sys, "platform", "linux")
    monkeypatch.setattr(ff, "_cfa_enabled", lambda: True)
    assert ff._cfa_hint(tmp_path / "out.m4a") is None


def test_repair_failure_includes_cfa_hint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ffmpeg fails to write output → repair failure message carries the hint."""
    audio = tmp_path / "test.m4a"
    audio.write_bytes(b"\x00" * 1000)
    monkeypatch.setattr(
        ff.subprocess, "run", lambda args, **kw: _completed(args, 1, "No such file or directory")
    )
    monkeypatch.setattr(ff, "_cfa_hint", lambda path: "[提示] CFA 拦截")
    result = ff.repair_audio_container(audio, "ffmpeg")
    assert result.success is False
    assert any("[提示] CFA 拦截" in t for _, t in result.messages)


def test_extract_failure_includes_cfa_hint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00")
    monkeypatch.setattr(
        ff.subprocess, "run", lambda args, **kw: _completed(args, 1, "No such file or directory")
    )
    monkeypatch.setattr(ff, "_cfa_hint", lambda path: "[提示] CFA 拦截")
    result = ff.extract_audio(video, tmp_path / "audio", "ffmpeg")
    assert result.success is False
    assert any("[提示] CFA 拦截" in t for _, t in result.messages)
