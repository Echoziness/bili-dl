"""Real dependency binaries and yt-dlp over loopback, without system tools."""

from __future__ import annotations

import functools
import http.server
import re
import struct
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from bili_dl import downloader, ffmpeg, media
from bili_dl.models import DownloadConfig

pytestmark = pytest.mark.media


def test_invalid_unicode_audio_keeps_original_and_returns_error(tmp_path: Path) -> None:
    executable = ffmpeg.find_ffmpeg()
    assert executable is not None
    audio = tmp_path / "损坏音频🌟.m4a"
    content = b"not valid media"
    audio.write_bytes(content)
    result = ffmpeg.repair_audio_container(audio, executable)
    assert not result.success
    assert audio.read_bytes() == content
    assert any(level == "error" and "保留原文件" in message for level, message in result.messages)
    assert not list(tmp_path.glob("_temp_*.m4a"))


def _run_ffmpeg(
    executable: str, *args: str, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [executable, "-y", "-hide_banner", "-loglevel", "error", *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
        cwd=cwd,
    )


@pytest.fixture(scope="module")
def samples(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, str]]:
    directory = tmp_path_factory.mktemp("managed-media")
    executable = ffmpeg.find_ffmpeg()
    assert executable is not None
    progressive = directory / "sample.mp4"
    _run_ffmpeg(
        executable,
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=128x96:rate=10",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440",
        "-t",
        "2",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        str(progressive),
    )
    _run_ffmpeg(
        executable,
        "-i",
        str(progressive),
        "-map",
        "0:v",
        "-map",
        "0:a",
        "-c",
        "copy",
        "-seg_duration",
        "1",
        "-f",
        "dash",
        str(directory / "sample.mpd"),
        cwd=directory,
    )
    _run_ffmpeg(
        executable,
        "-i",
        str(progressive),
        "-vn",
        "-c:a",
        "copy",
        "-movflags",
        "+dash",
        str(directory / "sample.m4a"),
    )

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0),
        functools.partial(Handler, directory=str(directory)),
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", executable
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def _assert_media(executable: str, path: Path, *, video: bool, audio: bool) -> None:
    assert path.is_file() and path.stat().st_size > 1000
    _run_ffmpeg(executable, "-xerror", "-i", str(path), "-f", "null", "-")
    inspection = subprocess.run(
        [executable, "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert ("Video:" in inspection.stderr) == video
    assert ("Audio:" in inspection.stderr) == audio
    duration = re.search(r"Duration: (\d+):(\d+):([\d.]+)", inspection.stderr)
    assert duration is not None
    assert (
        sum(float(t) * scale for t, scale in zip(duration.groups(), (3600, 60, 1), strict=True))
        >= 1.9
    )
    if not video:
        # Parse top-level ISO BMFF boxes; compressed media may contain these
        # byte sequences too, so searching raw bytes does not prove faststart.
        boxes = []
        contents = path.read_bytes()
        offset = 0
        while offset + 8 <= len(contents):
            size, kind = struct.unpack_from(">I4s", contents, offset)
            if size == 1:
                size = struct.unpack_from(">Q", contents, offset + 8)[0]
            boxes.append(kind)
            if size == 0:
                break
            assert size >= 8
            offset += size
        assert boxes.index(b"moov") < boxes.index(b"mdat")


@pytest.mark.parametrize(
    ("mode", "resource"),
    [
        ("v", "sample.mpd"),
        ("all", "sample.mpd"),
        ("a", "sample.mpd"),
        ("v", "sample.mp4"),
        ("all", "sample.mp4"),
        ("a", "sample.m4a"),
    ],
)
def test_managed_dependencies_download_and_process(
    samples: tuple[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    resource: str,
) -> None:
    base, executable = samples
    monkeypatch.setenv("PATH", "")
    monkeypatch.delenv("IMAGEIO_FFMPEG_EXE", raising=False)
    monkeypatch.chdir(tmp_path)
    # A nearby user's yt-dlp configuration must not change bili-dl behavior.
    (tmp_path / "yt-dlp.conf").write_text("--format nonexistent-format\n", encoding="utf-8")
    assert ffmpeg.find_ffmpeg() == executable
    assert media.find_ytdlp() is not None
    assert not (Path(executable).parent / "ffprobe").exists()
    assert not (Path(executable).parent / "ffprobe.exe").exists()
    cookie = tmp_path / "cookies.txt"
    cookie.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    stages = []
    config = DownloadConfig(
        mode,
        tmp_path / "video output",
        tmp_path / "audio output",
        cookie,
        ytdlp=media.find_ytdlp(),
        ffmpeg_bin=executable,
        stage=lambda phase, path: stages.append(phase),
        no_color=True,
    )
    result = downloader.download(f"{base}/{resource}", config)
    assert result.success, result.messages
    assert all(level != "error" for level, _ in result.messages), result.messages
    assert stages[:2] == ["prepare", "download"]
    assert result.output_path is not None
    _assert_media(executable, result.output_path, video=mode != "a", audio=True)
    if mode == "all":
        assert stages[-1] == "extract" and len(result.additional_paths) == 1
        _assert_media(executable, result.additional_paths[0], video=False, audio=True)
    elif mode == "a":
        assert stages[-1] == "repair"
