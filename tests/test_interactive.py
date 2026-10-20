"""Interactive journeys with real controllers and scripted user decisions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from bili_dl import actions, cli, cookiestore, interactive, settings
from bili_dl.actions import Options
from bili_dl.models import DownloadResult
from bili_dl.terminal import Back, Terminal

BV = "BV1Got26ZE5K"
URL = f"https://www.bilibili.com/video/{BV}"


class ScriptedTerminal(Terminal):
    def __init__(self, choices: list[Any], texts: list[str] | None = None) -> None:
        super().__init__()
        self.choices = iter(choices)
        self.texts = iter(texts or [])
        self.pauses = 0

    def pause(self, label: str = "返回首页") -> None:
        self.pauses += 1

    def select(self, title: str, choices: Any, **kwargs: Any) -> str:
        value = next(self.choices)
        if isinstance(value, BaseException):
            raise value
        assert value in dict(choices), (title, value)
        return value

    def text(self, title: str, **kwargs: Any) -> str:
        value = next(self.texts)
        if kwargs.get("validate"):
            assert kwargs["validate"](value) is True
        return value


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> interactive.Interactive:
    opts = Options(proxy="", cookie_dir=tmp_path / "session", video_dir=tmp_path / "files")
    app = interactive.Interactive(opts, tmp_path / "config.toml")
    monkeypatch.setattr(
        cookiestore, "ensure_cookie", lambda *a, **kw: cookiestore.EnsureResult(True)
    )
    monkeypatch.setattr(interactive.downloader, "find_ytdlp", lambda: "yt-dlp")
    monkeypatch.setattr(interactive.ff, "find_ffmpeg", lambda: "ffmpeg")
    return app


def test_home_requires_neither_network_nor_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("home must not require a downloader or contact Bilibili")

    monkeypatch.setattr(interactive.downloader, "find_ytdlp", forbidden)
    monkeypatch.setattr(cookiestore, "ensure_cookie", forbidden)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("bili_dl.ui.prompt", lambda p: "8")
    monkeypatch.setattr(cli, "_load_settings", lambda p: settings.Settings())
    assert cli.main([]) == 0


def test_subtitle_journey_keeps_resolved_options(app: Any, monkeypatch: Any) -> None:
    captured = []

    def download(opts: Options, url: str, **kwargs: Any) -> DownloadResult:
        captured.append((opts.mode, opts.proxy, opts.cookie_dir, url))
        kwargs["stage"]("subtitle", None)
        return DownloadResult(True, output_path=opts.video_dir / "字幕.srt")

    monkeypatch.setattr(actions, "download_media", download)
    app.terminal = ScriptedTerminal(["download", "s", "start", "home", "exit"], [BV])
    assert app.run() == 0
    assert captured == [("s", "", app.opts.cookie_dir, URL)]
    assert app.opts.mode == "all"  # A task override does not mutate session defaults.


@pytest.mark.parametrize("thread", [False, True])
def test_comment_journeys_forward_every_option(app: Any, monkeypatch: Any, thread: bool) -> None:
    captured = []

    def download(opts: Options, url: str, **kwargs: Any) -> DownloadResult:
        captured.append((opts, url, kwargs))
        kwargs["progress"](2, 50, 10000)
        return DownloadResult(True)

    monkeypatch.setattr(actions, "download_comments", download)
    choices = ["replies"] if thread else ["comments", "hot"]
    choices += ["limit", "full", "start", "home", "exit"]
    app.terminal = ScriptedTerminal(choices, [BV, "123", "50"] if thread else [BV, "50"])
    assert app.run() == 0
    opts, url, kwargs = captured[0]
    assert url == URL and opts.proxy == ""
    assert kwargs["limit"] == 50 and kwargs["full"] is True
    assert kwargs["root_id"] == ("123" if thread else None)
    assert kwargs["sort"] == ("newest" if thread else "hot")


def test_login_recovery_continues_original_task(app: Any, monkeypatch: Any) -> None:
    states = iter([False, True])
    logins = []
    downloads = []
    monkeypatch.setattr(
        cookiestore, "ensure_cookie", lambda *a, **kw: cookiestore.EnsureResult(next(states))
    )
    monkeypatch.setattr(
        actions, "login", lambda opts, **kw: logins.append(opts.proxy) or DownloadResult(True)
    )
    monkeypatch.setattr(
        actions,
        "download_media",
        lambda opts, url, **kw: downloads.append(url) or DownloadResult(True),
    )
    app.terminal = ScriptedTerminal(["download", "s", "start", "login", "home", "exit"], [BV])
    assert app.run() == 0
    assert logins == [""] and downloads == [URL]


def test_missing_cookie_never_implicitly_starts_login(app: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(
        cookiestore, "ensure_cookie", lambda *a, **kw: cookiestore.EnsureResult(False)
    )
    monkeypatch.setattr(actions, "login", lambda *a, **kw: pytest.fail("login needs a user choice"))
    app.terminal = ScriptedTerminal(["download", "s", "start", "back", "exit"], [BV])
    assert app.run() == 0


def test_batch_retry_only_repeats_failed_urls(app: Any, monkeypatch: Any, tmp_path: Path) -> None:
    batch = tmp_path / "video list.txt"
    batch.write_text(f"# sample\n{BV}\nav123\n", encoding="utf-8")
    calls = []

    def download(opts: Options, url: str, **kwargs: Any) -> DownloadResult:
        calls.append(url)
        return DownloadResult(len(calls) != 2)

    monkeypatch.setattr(actions, "download_media", download)
    app.terminal = ScriptedTerminal(["batch", "a", "start", "retry", "home", "exit"], [str(batch)])
    assert app.run() == 0
    assert calls == [
        URL,
        "https://www.bilibili.com/video/av123",
        "https://www.bilibili.com/video/av123",
    ]


@pytest.mark.parametrize("signal", [KeyboardInterrupt(), Back(), EOFError()])
def test_cancel_and_eof_restore_terminal(app: Any, signal: BaseException) -> None:
    app.terminal = ScriptedTerminal(["download", signal, "exit"], [BV])
    assert app.run() == 0


def test_interrupt_during_download_returns_home(app: Any, monkeypatch: Any) -> None:
    def interrupt(*args: Any, **kwargs: Any) -> Any:
        kwargs["stage"]("prepare", None)
        raise KeyboardInterrupt

    monkeypatch.setattr(actions, "download_media", interrupt)
    app.terminal = ScriptedTerminal(["download", "s", "start", "exit"], [BV])
    assert app.run() == 0


def test_missing_ytdlp_does_not_block_other_menu_items(app: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(interactive.downloader, "find_ytdlp", lambda: None)
    monkeypatch.setattr(
        cookiestore,
        "ensure_cookie",
        lambda *a, **kw: pytest.fail("no cookie check before tool check"),
    )
    app.terminal = ScriptedTerminal(["download", "v", "start", "help", "exit"], [BV])
    assert app.run() == 0


def test_explicit_config_export_preserves_existing_file(app: Any, tmp_path: Path) -> None:
    original = '# keep this comment\nproxy = ""\n'
    app.config_path.write_text(original, encoding="utf-8")
    destination = tmp_path / "new config.toml"
    app.terminal = ScriptedTerminal([], [str(destination)])
    app.export_settings(app.opts)
    assert app.config_path.read_text(encoding="utf-8") == original
    assert settings.load(destination).proxy == ""
    assert settings.load(destination).video_dir == app.opts.video_dir


def test_export_omits_proxy_authentication(app: Any, tmp_path: Path, capsys: Any) -> None:
    destination = tmp_path / "safe.toml"
    app.opts.proxy = "http://user:private-secret@example.com:8080"
    app.terminal = ScriptedTerminal([], [str(destination)])
    app.export_settings(app.opts)
    assert "private-secret" not in destination.read_text(encoding="utf-8")
    assert "private-secret" not in capsys.readouterr().err


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (BV, URL),
        (f"看这个视频 {URL}。", URL),
        ("av123", "https://www.bilibili.com/video/av123"),
        ("https://b23.tv/abc", "https://b23.tv/abc"),
    ],
)
def test_reference_accepts_identifiers_and_share_text(value: str, expected: str) -> None:
    assert interactive.reference(value) == expected


@pytest.mark.parametrize(
    "value",
    ["https://example.com/video/" + BV, "https://u:p@www.bilibili.com/video/" + BV, "BVwrong", ""],
)
def test_reference_rejects_unrelated_or_credential_urls(value: str) -> None:
    assert isinstance(interactive.valid_reference(value), str)


def test_session_settings_and_status_share_config(app: Any, monkeypatch: Any) -> None:
    calls = []
    app.terminal = ScriptedTerminal(
        ["settings", "proxy", "direct", "back", "account", "status", "exit"]
    )
    monkeypatch.setattr(
        actions, "session_status", lambda opts: calls.append(opts.proxy) or DownloadResult(True)
    )
    assert app.run() == 0
    assert calls == [""]
    assert app.terminal.pauses == 1


def test_information_page_waits_before_home(app: Any) -> None:
    app.terminal = ScriptedTerminal(["help", "exit"])
    assert app.run() == 0
    assert app.terminal.pauses == 1
