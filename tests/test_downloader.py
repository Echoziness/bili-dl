"""All download modes share one dispatcher and prepare only their output paths."""

from pathlib import Path

import pytest

from bili_dl import downloader, media, subtitles
from bili_dl.models import DownloadConfig, DownloadResult


@pytest.mark.parametrize("mode", ["all", "v", "a", "s"])
def test_dispatch_prepares_only_relevant_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    cfg = DownloadConfig(mode, tmp_path / "video", tmp_path / "audio", tmp_path / "cookie")
    calls = []

    def run(url: str, config: DownloadConfig) -> DownloadResult:
        calls.append((url, config))
        assert cfg.video_dir.exists() == (mode != "a")
        assert cfg.audio_dir.exists() == (mode in {"all", "a"})
        return DownloadResult(True)

    backend, other = (subtitles, media) if mode == "s" else (media, subtitles)
    monkeypatch.setattr(backend, "download", run)
    monkeypatch.setattr(other, "download", lambda *args: pytest.fail("Wrong backend"))
    assert downloader.download("video-url", cfg).success
    assert calls == [("video-url", cfg)]


def test_output_error_stops_before_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blocked = tmp_path / "file"
    blocked.write_text("not a directory", encoding="utf-8")
    cfg = DownloadConfig("s", blocked, tmp_path / "audio", tmp_path / "cookie")
    monkeypatch.setattr(subtitles, "download", lambda *args: pytest.fail("Must not fetch"))
    result = downloader.download("video-url", cfg)
    assert not result.success
    assert "输出目录" in str(result.messages)
