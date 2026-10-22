"""Tests for yt-dlp argument assembly and download orchestration.

Part 1 — shared subprocess arguments.

Part 2 — download() with mocked subprocess: Phase 1 predict failure,
Phase 2 failure, success paths for all three modes.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from bili_dl import media
from bili_dl.config import REFERER
from bili_dl.models import DownloadConfig


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
    args = media._common_args(cfg)
    assert "--no-playlist" in args
    assert "--ignore-config" in args
    assert "--retries" in args
    idx = args.index("--cookies")
    assert args[idx + 1] == str(cfg.cookie_path)
    idx = args.index("--add-header")
    assert args[idx + 1] == f"Referer:{REFERER}"
    idx = args.index("--proxy")
    assert args[idx + 1] == ""  # yt-dlp's documented direct-connection value
    assert "--no-check-certificate" not in args


def test_common_args_proxy(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, proxy="http://127.0.0.1:7890")
    args = media._common_args(cfg)
    idx = args.index("--proxy")
    assert args[idx + 1] == "http://127.0.0.1:7890"


def test_common_args_insecure(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, insecure=True)
    args = media._common_args(cfg)
    assert "--no-check-certificate" in args


def test_unavailable_explicit_ffmpeg_prevents_child_system_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = str(tmp_path / "missing-ffmpeg")
    monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", missing)
    cfg = _cfg(tmp_path)
    args = media._common_args(cfg)
    assert args[args.index("--ffmpeg-location") + 1] == missing


def test_dependency_command_uses_current_python(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    assert media.find_ytdlp() == (sys.executable, "-m", "yt_dlp")
    monkeypatch.setattr(media.importlib.util, "find_spec", lambda name: None)
    assert media.find_ytdlp() is None


@pytest.mark.parametrize("stdout", ["part1.flv\npart2.flv\n", "same.flv\nsame.flv\n"])
def test_multiple_targets_stop_before_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: str
) -> None:
    calls = []

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls.append(args)
        return _completed_process(args, 0, stdout)

    monkeypatch.setattr(media.subprocess, "run", run)
    cfg = _cfg(tmp_path)
    cfg.ytdlp = (sys.executable, "-m", "yt_dlp")
    result = media.download("https://www.bilibili.com/video/BV1", cfg)
    assert not result.success and result.output_path is None
    assert len(calls) == 1 and "--skip-download" in calls[0]
    assert any("多个输出目标" in message for _, message in result.messages)


def test_both_phases_use_same_command_and_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "video.mp4"
    cfg = _cfg(tmp_path)
    cfg.mode = "v"
    cfg.ytdlp = (sys.executable, "-m", "yt_dlp")
    cfg.ffmpeg_bin = str(tmp_path / "space dir" / "ffmpeg-version.exe")
    calls = []

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls.append(args)
        if "--skip-download" in args:
            return _completed_process(args, 0, str(output))
        output.write_bytes(b"video")
        return _completed_process(args, 0)

    monkeypatch.setattr(media.subprocess, "run", run)
    assert media.download("https://www.bilibili.com/video/BV1", cfg).success
    assert len(calls) == 2
    for args in calls:
        assert args[:3] == list(cfg.ytdlp)
        assert args[args.index("--ffmpeg-location") + 1] == cfg.ffmpeg_bin


# ─── Part 2: download() with mocked subprocess ──────────────────────────────


def _completed_process(
    args: list[str], returncode: int, stdout: str = ""
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="")


def test_download_no_ytdlp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(media, "find_ytdlp", lambda: None)
    cfg = _cfg(tmp_path)
    result = media.download("https://bilibili.com/video/BV1", cfg)
    assert result.success is False
    assert any("yt-dlp" in t for _, t in result.messages)


def test_download_phase1_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(media, "find_ytdlp", lambda: ("python", "-m", "yt_dlp"))

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        return _completed_process(args, 1)

    monkeypatch.setattr(media.subprocess, "run", fake_run)
    cfg = DownloadConfig(
        mode="all",
        video_dir=tmp_path / "v",
        audio_dir=tmp_path / "a",
        cookie_path=tmp_path / "c.txt",
        ytdlp=("python", "-m", "yt_dlp"),
    )
    result = media.download("https://bilibili.com/video/BV1", cfg)
    assert result.success is False


def test_download_phase2_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(media, "find_ytdlp", lambda: ("python", "-m", "yt_dlp"))
    out_file = tmp_path / "v" / "title.mp4"
    calls = [0]

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls[0] += 1
        if calls[0] == 1:  # phase 1 predict
            return _completed_process(args, 0, stdout=str(out_file))
        # phase 2: return failure, don't create file
        return _completed_process(args, 1)

    monkeypatch.setattr(media.subprocess, "run", fake_run)
    cfg = DownloadConfig(
        mode="v",
        video_dir=tmp_path / "v",
        audio_dir=tmp_path / "a",
        cookie_path=tmp_path / "c.txt",
        ytdlp=("python", "-m", "yt_dlp"),
        ffmpeg_bin=None,
    )
    result = media.download("https://bilibili.com/video/BV1", cfg)
    assert result.success is False


def test_download_phase2_nonzero_but_file_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """yt-dlp returns non-zero but writes the file → warn, not fail."""
    monkeypatch.setattr(media, "find_ytdlp", lambda: ("python", "-m", "yt_dlp"))
    out_file = tmp_path / "v" / "title.mp4"
    calls = [0]

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls[0] += 1
        if calls[0] == 1:
            return _completed_process(args, 0, stdout=str(out_file))
        # phase 2: return non-zero, but create the file (yt-dlp warning scenario)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_bytes(b"\x00")
        return _completed_process(args, 1)

    monkeypatch.setattr(media.subprocess, "run", fake_run)
    cfg = DownloadConfig(
        mode="v",
        video_dir=tmp_path / "v",
        audio_dir=tmp_path / "a",
        cookie_path=tmp_path / "c.txt",
        ytdlp=("python", "-m", "yt_dlp"),
        ffmpeg_bin=None,
    )
    result = media.download("https://bilibili.com/video/BV1", cfg)
    assert result.success is True
    assert any("退出码" in t for _, t in result.messages)


def test_download_video_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(media, "find_ytdlp", lambda: ("python", "-m", "yt_dlp"))
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

    monkeypatch.setattr(media.subprocess, "run", fake_run)
    cfg = DownloadConfig(
        mode="v",
        video_dir=tmp_path / "v",
        audio_dir=tmp_path / "a",
        cookie_path=tmp_path / "c.txt",
        ytdlp=("python", "-m", "yt_dlp"),
        ffmpeg_bin=None,
    )
    result = media.download("https://bilibili.com/video/BV1", cfg)
    assert result.success is True
    assert result.output_path == out_file


def test_download_audio_no_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(media, "find_ytdlp", lambda: ("python", "-m", "yt_dlp"))
    out_file = tmp_path / "a" / "title.m4a"
    calls = [0]

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls[0] += 1
        if calls[0] == 1:
            return _completed_process(args, 0, stdout=str(out_file))
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_bytes(b"\x00")
        return _completed_process(args, 0)

    monkeypatch.setattr(media.subprocess, "run", fake_run)
    cfg = DownloadConfig(
        mode="a",
        video_dir=tmp_path / "v",
        audio_dir=tmp_path / "a",
        cookie_path=tmp_path / "c.txt",
        ytdlp=("python", "-m", "yt_dlp"),
        ffmpeg_bin=None,
    )
    result = media.download("https://bilibili.com/video/BV1", cfg)
    assert result.success is True
    # Should have a "skip" warning for no ffmpeg
    assert any("跳过" in t for _, t in result.messages)


def test_download_all_no_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(media, "find_ytdlp", lambda: ("python", "-m", "yt_dlp"))
    out_file = tmp_path / "v" / "title.mp4"
    calls = [0]

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls[0] += 1
        if calls[0] == 1:
            return _completed_process(args, 0, stdout=str(out_file))
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_bytes(b"\x00")
        return _completed_process(args, 0)

    monkeypatch.setattr(media.subprocess, "run", fake_run)
    cfg = DownloadConfig(
        mode="all",
        video_dir=tmp_path / "v",
        audio_dir=tmp_path / "a",
        cookie_path=tmp_path / "c.txt",
        ytdlp=("python", "-m", "yt_dlp"),
        ffmpeg_bin=None,
    )
    result = media.download("https://bilibili.com/video/BV1", cfg)
    assert result.success is True
    # Should have a "skip" warning for audio extraction
    assert any("跳过" in t for _, t in result.messages)


def test_download_all_with_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(media, "find_ytdlp", lambda: ("python", "-m", "yt_dlp"))
    out_file = tmp_path / "v" / "title.mp4"
    calls = [0]

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls[0] += 1
        if calls[0] == 1:
            return _completed_process(args, 0, stdout=str(out_file))
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_bytes(b"\x00")
        return _completed_process(args, 0)

    monkeypatch.setattr(media.subprocess, "run", fake_run)

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
        ytdlp=("python", "-m", "yt_dlp"),
        ffmpeg_bin="ffmpeg",
    )
    result = media.download("https://bilibili.com/video/BV1", cfg)
    assert result.success is True
    assert any("提取音频" in t for _, t in result.messages)


def test_download_phase1_warns_but_proceeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 1 rc != 0 with valid stdout → warn, not fail."""
    monkeypatch.setattr(media, "find_ytdlp", lambda: ("python", "-m", "yt_dlp"))
    out_file = tmp_path / "v" / "title.mp4"
    calls = [0]

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls[0] += 1
        if calls[0] == 1:  # Phase 1: rc=3 (warning) but stdout has valid path
            return _completed_process(args, 3, stdout=str(out_file))
        # Phase 2: create file, rc=0
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_bytes(b"\x00")
        return _completed_process(args, 0)

    monkeypatch.setattr(media.subprocess, "run", fake_run)
    cfg = DownloadConfig(
        mode="v",
        video_dir=tmp_path / "v",
        audio_dir=tmp_path / "a",
        cookie_path=tmp_path / "c.txt",
        ytdlp=("python", "-m", "yt_dlp"),
        ffmpeg_bin=None,
    )
    result = media.download("https://bilibili.com/video/BV1", cfg)
    assert result.success is True
    assert any("退出码 3" in t for _, t in result.messages)
    assert any("predict" in t for _, t in result.messages)


def test_download_predict_uses_utf8_for_non_cp936_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prediction must preserve characters that Windows CP936 cannot encode."""
    out_file = tmp_path / "a" / "诗岸⧸洛天依.m4a"
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls.append((args, kwargs))
        if len(calls) == 1:
            return _completed_process(args, 0, stdout=str(out_file))
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_bytes(b"\x00")
        return _completed_process(args, 0)

    monkeypatch.setattr(media.subprocess, "run", fake_run)
    cfg = DownloadConfig(
        mode="a",
        video_dir=tmp_path / "v",
        audio_dir=tmp_path / "a",
        cookie_path=tmp_path / "c.txt",
        ytdlp=("python", "-m", "yt_dlp"),
        ffmpeg_bin=None,
    )

    result = media.download("https://bilibili.com/video/BV1", cfg)

    assert result.success is True
    predict_args, predict_kwargs = calls[0]
    encoding_index = predict_args.index("--encoding")
    assert predict_args[encoding_index + 1] == "utf-8"
    assert predict_kwargs.get("encoding") == "utf-8"
    assert "--encoding" not in calls[1][0]


def test_download_child_resolves_its_own_pythonhome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The module child resolves stdlib from its own environment."""
    monkeypatch.setenv("PYTHONHOME", str(tmp_path / "uv-python"))
    monkeypatch.setenv("BILI_DL_TEST_ENV", "preserved")
    out_file = tmp_path / "a" / "title.m4a"
    calls: list[dict[str, object]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls.append(kwargs)
        if len(calls) == 1:
            return _completed_process(args, 0, stdout=str(out_file))
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_bytes(b"\x00")
        return _completed_process(args, 0)

    monkeypatch.setattr(media.subprocess, "run", fake_run)
    cfg = DownloadConfig(
        mode="a",
        video_dir=tmp_path / "v",
        audio_dir=tmp_path / "a",
        cookie_path=tmp_path / "c.txt",
        ytdlp=("python", "-m", "yt_dlp"),
        ffmpeg_bin=None,
    )

    result = media.download("https://bilibili.com/video/BV1", cfg)

    assert result.success is True
    assert len(calls) == 2
    for kwargs in calls:
        env = kwargs.get("env")
        assert isinstance(env, dict)
        assert "PYTHONHOME" not in env
        assert env["BILI_DL_TEST_ENV"] == "preserved"


# ─── find_ytdlp + audio mode with ffmpeg repair ──────────────────────────────


def test_download_audio_with_ffmpeg_repair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Direct audio downloads also pass through container repair."""
    monkeypatch.setattr(media, "find_ytdlp", lambda: ("python", "-m", "yt_dlp"))
    out_file = tmp_path / "a" / "title.m4a"
    calls = [0]

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls[0] += 1
        if calls[0] == 1:
            return _completed_process(args, 0, stdout=str(out_file))
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_bytes(b"\x00")
        return _completed_process(args, 0)

    monkeypatch.setattr(media.subprocess, "run", fake_run)

    from bili_dl import ffmpeg as ff

    repair_called: list[Path] = []

    def fake_repair(path: Path, ffmpeg_bin: str) -> ff.RepairResult:
        repair_called.append(path)
        return ff.RepairResult(success=True, messages=[("ok", "[完成!] 容器已修复")])

    monkeypatch.setattr(ff, "repair_audio_container", fake_repair)
    cfg = DownloadConfig(
        mode="a",
        video_dir=tmp_path / "v",
        audio_dir=tmp_path / "a",
        cookie_path=tmp_path / "c.txt",
        ytdlp=("python", "-m", "yt_dlp"),
        ffmpeg_bin="ffmpeg",
    )
    result = media.download("https://bilibili.com/video/BV1", cfg)
    assert result.success is True
    assert repair_called == [out_file]
    assert any("容器已修复" in t for _, t in result.messages)


def test_interactive_stages_keep_native_progress_and_utf8_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_file = tmp_path / "video.mp4"
    audio = tmp_path / "audio.m4a"
    stages = []
    calls = []

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls.append((args, kwargs))
        if len(calls) == 1:
            assert stages == [("prepare", None)]
            assert kwargs["capture_output"] is True
            assert kwargs["encoding"] == "utf-8"
            return _completed_process(args, 0, str(out_file))
        assert stages[-1] == ("download", out_file)
        assert "--quiet" in args and "--progress" in args
        assert args[args.index("--color") + 1] == "never"
        assert "capture_output" not in kwargs and "stdout" not in kwargs and "stderr" not in kwargs
        assert "--encoding" not in args
        out_file.write_bytes(b"video")
        return _completed_process(args, 0)

    def extract(*args: object) -> object:
        assert stages[-1] == ("extract", out_file)
        audio.write_bytes(b"audio")
        from bili_dl.ffmpeg import ExtractResult

        return ExtractResult(True, audio_path=audio)

    monkeypatch.setattr(media.subprocess, "run", run)
    monkeypatch.setattr(media.ff, "extract_audio", extract)
    cfg = _cfg(tmp_path)
    cfg.ytdlp, cfg.ffmpeg_bin = ("python", "-m", "yt_dlp"), "ffmpeg"
    cfg.stage = lambda name, path: stages.append((name, path))
    cfg.no_color = True
    result = media.download("https://bilibili.com/video/BV1", cfg)
    assert result.success and result.additional_paths == [audio]
