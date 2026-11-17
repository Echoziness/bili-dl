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
        encoding="utf-8",
        errors="replace",
        check=True,
        timeout=30,
        cwd=cwd,
    )


@pytest.fixture(scope="module")
def samples(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, str, Path]]:
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
    for codec in ("flac", "eac3", "alac"):
        video = directory / f"premium-{codec}.mp4"
        # Synthetic E-AC-3 tests six-channel container support, not Atmos/JOC
        # metadata. Lossless samples exercise 24-bit audio at higher rates.
        audio_options = (
            ["-ac", "6", "-ar", "48000"]
            if codec == "eac3"
            else ["-sample_fmt", "s32", "-ar", "96000"]
        )
        _run_ffmpeg(
            executable,
            "-i",
            str(progressive),
            "-c:v",
            "copy",
            "-c:a",
            codec,
            *audio_options,
            str(video),
        )
        _run_ffmpeg(
            executable,
            "-i",
            str(video),
            "-vn",
            "-c:a",
            "copy",
            *(["-bsf:a", "setts=duration=0"] if codec == "flac" else []),
            "-movflags",
            "+frag_keyframe+delay_moov" if codec == "eac3" else "+dash",
            "-f",
            "mp4",
            str(directory / f"premium-{codec}.m4a"),
        )
    _run_ffmpeg(
        executable,
        "-i",
        str(directory / "premium-flac.mp4"),
        "-i",
        str(directory / "premium-flac.m4a"),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c",
        "copy",
        str(directory / "premium-flac-with-edits.mp4"),
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
        yield f"http://127.0.0.1:{server.server_port}", executable, directory
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def _audio_hash(executable: str, path: Path, codec: str) -> str:
    return _run_ffmpeg(
        executable,
        "-i",
        str(path),
        "-map",
        "0:a:0",
        "-c:a",
        codec,
        "-f",
        "hash",
        "-hash",
        "sha256",
        "-",
    ).stdout.strip()


def test_repair_preserves_aac_encoder_delay(tmp_path: Path) -> None:
    executable = ffmpeg.find_ffmpeg()
    assert executable is not None
    path = tmp_path / "aac-with-priming.m4a"
    _run_ffmpeg(
        executable,
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000",
        "-t",
        "2",
        "-c:a",
        "aac",
        str(path),
    )
    before = _audio_hash(executable, path, "pcm_s32le")
    result = ffmpeg.repair_audio_container(path, executable)
    assert result.success, result.messages
    assert before == _audio_hash(executable, path, "pcm_s32le")
    _assert_media(executable, path, video=False, audio=True)


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
        ("a", "premium-flac.m4a"),
        ("all", "premium-flac.mp4"),
        ("a", "premium-eac3.m4a"),
        ("all", "premium-eac3.mp4"),
        ("a", "premium-alac.m4a"),
        ("all", "premium-alac.mp4"),
        ("all", "premium-flac-with-edits.mp4"),
        ("v", "premium-flac-with-edits.mp4"),
    ],
)
def test_managed_dependencies_download_and_process(
    samples: tuple[str, str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    resource: str,
) -> None:
    base, executable, source_directory = samples
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
    if resource.startswith("premium-"):
        audio = result.additional_paths[0] if mode == "all" else result.output_path
        expected_codec = Path(resource).stem.split("-")[1]
        inspection = subprocess.run(
            [executable, "-hide_banner", "-i", str(audio)],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        assert f"Audio: {expected_codec}" in inspection.stderr
        if expected_codec == "eac3":
            assert "48000 Hz, 5.1" in inspection.stderr
        else:
            assert "96000 Hz" in inspection.stderr and "24 bit" in inspection.stderr
        # Compare both compressed packets and decoded PCM, so success cannot
        # silently mean transcoding or changing sample precision/channels.
        for audio_codec in ("copy", "pcm_s32le"):
            reference = source_directory / (
                "premium-flac.m4a" if "with-edits" in resource else resource
            )
            hashes = [_audio_hash(executable, target, audio_codec) for target in (reference, audio)]
            assert hashes[0] and hashes[0] == hashes[1]
        if "with-edits" in resource:
            # Video data and presentation timing must survive the FLAC fix;
            # decoding timestamps may change to use signed CTS offsets.
            frames = [
                _run_ffmpeg(
                    executable,
                    "-i",
                    str(path),
                    "-map",
                    "0:v:0",
                    "-c",
                    "copy",
                    "-f",
                    "framehash",
                    "-",
                ).stdout.splitlines()
                for path in (source_directory / resource, result.output_path)
            ]
            signatures = [
                [
                    (row.split(",")[2], row.rsplit(",", 1)[1])
                    for row in rows
                    if not row.startswith("#")
                ]
                for rows in frames
            ]
            assert signatures[0] and signatures[0] == signatures[1]
