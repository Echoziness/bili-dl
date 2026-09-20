"""Subtitle selection, timestamp fidelity and safe failure behavior."""

from __future__ import annotations

import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from bili_dl import cli, subtitles, transport
from bili_dl.models import DownloadConfig

VIDEO = "https://www.bilibili.com/video/BV1Got26ZE5K/"


@pytest.fixture
def cfg(tmp_path: Path) -> DownloadConfig:
    cookie = tmp_path / "cookies_bilibili.txt"
    cookie.write_text(
        "# Netscape HTTP Cookie File\n.bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\tfake-session\n",
        encoding="utf-8",
    )
    output = tmp_path / "subtitles"
    output.mkdir()
    return DownloadConfig("s", output, tmp_path / "unused-audio", cookie)


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {
        "info": {
            "code": 0,
            "data": {
                "aid": 123,
                "bvid": "BV1Got26ZE5K",
                "title": "中文⧸标题:示例",
                "pages": [{"cid": 456}, {"cid": 789}],
            },
        },
        "player": {
            "code": 0,
            "data": {
                "subtitle": {
                    "subtitles": [
                        {
                            "lan": "ai-en",
                            "subtitle_url": "//aisubtitle.hdslb.com/first?auth_key=secret",
                        },
                        {"lan": "zh-CN", "subtitle_url": "//aisubtitle.hdslb.com/second"},
                    ]
                }
            },
        },
        "body": {
            "body": [
                {"from": 0, "to": 1.2345, "content": "第一句"},
                {"from": 59.9995, "to": 3600.001, "content": "第二句\r\n\r\n换行"},
            ]
        },
        "calls": [],
    }

    def fetch(opener: Any, request: Any, timeout: float) -> tuple[Any, Any]:
        state["calls"].append((opener, request.full_url, timeout))
        path = urllib.parse.urlsplit(request.full_url).path
        if path == "/x/web-interface/view":
            return state["info"], None
        if path == "/x/player/wbi/v2":
            return state["player"], None
        assert path == "/first", "Must never request a later subtitle track"
        if state.get("cdn_failure"):
            return None, transport.HttpFailure("http", 403)
        return state["body"], None

    monkeypatch.setattr(transport, "fetch_json", fetch)
    return state


def test_first_returned_track_and_requested_part_are_preserved(
    cfg: DownloadConfig,
    api: dict[str, Any],
) -> None:
    cfg.proxy = "http://selected-proxy:7890"
    cfg.insecure = True  # This switch must not disable TLS for authenticated APIs.
    result = subtitles.download(VIDEO + "?p=2", cfg)

    assert result.success, result.messages
    assert result.output_path is not None
    assert result.output_path.name.endswith("p2.ai-en.srt")
    assert "中文⧸标题_示例" in result.output_path.name
    assert result.output_path.read_text(encoding="utf-8") == (
        "1\n00:00:00,000 --> 00:00:01,235\n第一句\n\n"
        "2\n00:01:00,000 --> 01:00:00,001\n第二句\n换行\n\n"
    )
    assert len(api["calls"]) == 3
    _, player_url, _ = api["calls"][1]
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(player_url).query) == {
        "aid": ["123"],
        "cid": ["789"],
    }
    authenticated = api["calls"][0][0]
    public = api["calls"][2][0]
    assert any(isinstance(h, urllib.request.HTTPCookieProcessor) for h in authenticated.handlers)
    assert not any(isinstance(h, urllib.request.HTTPCookieProcessor) for h in public.handlers)
    for opener in (authenticated, public):
        proxy = next(h for h in opener.handlers if isinstance(h, urllib.request.ProxyHandler))
        assert proxy.proxies["https"] == cfg.proxy
        assert not any(
            isinstance(h, urllib.request.HTTPSHandler) and h._context is not None
            for h in opener.handlers
        )
    assert "secret" not in repr(result)


@pytest.mark.parametrize(
    ("player", "message"),
    [
        ({"code": 0, "data": {"subtitle": {"subtitles": []}}}, "没有可提取"),
        ({"code": 0, "data": {"need_login_subtitle": True}}, "需要有效登录"),
        ({"code": -101}, "登录已失效"),
        ({"code": -352, "message": "secret-response"}, "错误码 -352"),
        ({"code": 0, "data": {}}, "字幕列表格式异常"),
    ],
)
def test_unavailable_subtitles_are_not_reported_as_success(
    cfg: DownloadConfig,
    api: dict[str, Any],
    player: dict[str, Any],
    message: str,
) -> None:
    api["player"] = player
    result = subtitles.download(VIDEO, cfg)
    assert not result.success
    assert message in str(result.messages)
    assert "secret" not in str(result.messages)
    assert len(api["calls"]) == 2
    assert not list(cfg.video_dir.iterdir())


@pytest.mark.parametrize("failure", ["empty_url", "http", "invalid_body"])
def test_first_track_failure_never_falls_back_or_overwrites_existing_output(
    cfg: DownloadConfig,
    api: dict[str, Any],
    failure: str,
) -> None:
    previous = subtitles.download(VIDEO, cfg)
    path = previous.output_path
    assert path is not None
    path.write_text("previous valid subtitle", encoding="utf-8")
    api["calls"].clear()
    if failure == "empty_url":
        api["player"]["data"]["subtitle"]["subtitles"][0]["subtitle_url"] = ""
    elif failure == "http":
        api["cdn_failure"] = True
    else:
        api["body"] = {"body": [{"from": 0, "to": -1, "content": "invalid"}]}
    result = subtitles.download(VIDEO, cfg)
    assert not result.success
    assert path.read_text(encoding="utf-8") == "previous valid subtitle"
    assert len(list(cfg.video_dir.iterdir())) == 1
    assert "secret" not in repr(result)


def test_atomic_replace_failure_preserves_previous_file(
    cfg: DownloadConfig,
    api: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = subtitles.download(VIDEO, cfg).output_path
    assert path is not None
    original = path.read_bytes()

    def fail_replace(self: Path, target: Path) -> None:
        raise PermissionError("secret diagnostic")

    monkeypatch.setattr(Path, "replace", fail_replace)
    result = subtitles.download(VIDEO, cfg)
    assert not result.success
    assert path.read_bytes() == original
    assert list(cfg.video_dir.iterdir()) == [path]
    assert "secret" not in repr(result)


def test_share_link_resolves_without_session_and_keeps_part(
    cfg: DownloadConfig,
    api: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def resolve(opener: Any, url: str, timeout: float) -> tuple[str, None]:
        assert url == "https://b23.tv/example"
        assert not any(isinstance(h, urllib.request.HTTPCookieProcessor) for h in opener.handlers)
        return VIDEO + "?p=2", None

    monkeypatch.setattr(transport, "resolve_url", resolve)
    result = subtitles.download("https://b23.tv/example", cfg)
    assert result.success
    assert result.output_path is not None and "p2" in result.output_path.name


@pytest.mark.parametrize(
    "value", [VIDEO + "?p=0", VIDEO + "?p=no", "https://example.com/video/BV1Got26ZE5K/"]
)
def test_invalid_input_stops_before_authenticated_requests(
    cfg: DownloadConfig,
    api: dict[str, Any],
    value: str,
) -> None:
    assert not subtitles.download(value, cfg).success
    assert api["calls"] == []


def test_missing_part_stops_before_requesting_subtitle_list(
    cfg: DownloadConfig,
    api: dict[str, Any],
) -> None:
    result = subtitles.download(VIDEO + "?p=3", cfg)
    assert not result.success
    assert len(api["calls"]) == 1


def test_long_unicode_title_fits_cross_platform_filename_limits(
    cfg: DownloadConfig,
    api: dict[str, Any],
) -> None:
    api["info"]["data"]["title"] = "中文标题" * 100
    result = subtitles.download(VIDEO, cfg)
    assert result.success
    assert result.output_path is not None
    assert len(result.output_path.name.encode("utf-8")) <= 255


def test_batch_preserves_success_when_another_video_has_no_subtitles(
    cfg: DownloadConfig,
    api: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "_prepare_cookie", lambda opts: True)
    batch = cfg.cookie_path.parent / "urls.txt"
    batch.write_text(VIDEO + "\nhttps://www.bilibili.com/video/BV1Tct1ztE26/\n", encoding="utf-8")
    fetch = transport.fetch_json

    def fail_second(opener: Any, request: Any, timeout: float) -> tuple[Any, Any]:
        if "BV1Tct1ztE26" in request.full_url:
            return {"code": -404}, None
        return fetch(opener, request, timeout)

    monkeypatch.setattr(transport, "fetch_json", fail_second)
    assert (
        cli.main(
            [
                "-s",
                "--batch-file",
                str(batch),
                "--cookie-dir",
                str(cfg.cookie_path.parent),
                "--output-dir",
                str(cfg.video_dir),
            ]
        )
        == 1
    )
    assert len(list(cfg.video_dir.glob("*.srt"))) == 1
    assert "1 成功, 1 失败" in capsys.readouterr().err


@pytest.mark.parametrize("start,end", [(float("nan"), 1), (0, float("inf")), (True, 1), (2, 1)])
def test_invalid_timestamps_are_rejected(start: Any, end: Any) -> None:
    with pytest.raises(subtitles.SubtitleError):
        subtitles._to_srt([{"from": start, "to": end, "content": "text"}])


@pytest.mark.parametrize("flag", ["-s", "--subtitle", "config"])
def test_subtitle_cli_uses_shared_options_without_media_tools(
    cfg: DownloadConfig,
    api: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    flag: str,
) -> None:
    monkeypatch.setattr(cli, "_prepare_cookie", lambda opts: True)
    monkeypatch.setattr(cli.downloader, "find_ytdlp", lambda: pytest.fail("No yt-dlp needed"))
    monkeypatch.setattr(cli.ff, "find_ffmpeg", lambda: pytest.fail("No ffmpeg needed"))
    args = [
        "--cookie-dir",
        str(cfg.cookie_path.parent),
        "--output-dir",
        str(cfg.video_dir),
        "--audio-dir",
        str(cfg.audio_dir),
        VIDEO,
    ]
    if flag == "config":
        config = cfg.cookie_path.parent / "config.toml"
        config.write_text('mode = "s"\n', encoding="utf-8")
        args[:0] = ["--config", str(config)]
    else:
        args.insert(0, flag)
    assert cli.main(args) == 0
    assert len(list(cfg.video_dir.glob("*.srt"))) == 1
    assert not cfg.audio_dir.exists()
    output = capsys.readouterr()
    assert output.out == ""
    assert "ffmpeg" not in output.err and "None" not in output.err
