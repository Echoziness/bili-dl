"""Tests for CLI argument parsing, config loading, batch download, and main() flow.

Guards against regressions in flag threading, config-file merging,
batch-file parsing, and dependency checking — all without invoking
any network or subprocess.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bili_dl import cli, downloader, settings
from bili_dl import ffmpeg as ff
from bili_dl.cli import _build_parser


def _parse(*argv: str):
    return _build_parser().parse_args(list(argv))


def test_default_mode_is_none() -> None:
    args = _parse()
    assert args.mode is None
    assert args.url is None


def test_video_mode() -> None:
    assert _parse("-v", "https://bilibili.com/video/BV123").mode == "v"


def test_audio_mode() -> None:
    assert _parse("-a", "https://bilibili.com/video/BV123").mode == "a"


def test_all_mode() -> None:
    assert _parse("--all", "https://bilibili.com/video/BV123").mode == "all"


def test_mode_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        _parse("-v", "-a", "https://bilibili.com/video/BV123")


def test_proxy() -> None:
    args = _parse("--proxy", "http://127.0.0.1:7890", "https://bilibili.com/video/BV123")
    assert args.proxy == "http://127.0.0.1:7890"


def test_insecure() -> None:
    assert _parse("-k", "https://bilibili.com/video/BV123").insecure is True


def test_cookie_dir() -> None:
    assert _parse("--cookie-dir", "/tmp/cookies", "https://x").cookie_dir == Path("/tmp/cookies")


def test_output_dir() -> None:
    assert _parse("--output-dir", "/tmp/videos", "https://x").output_dir == Path("/tmp/videos")


def test_audio_dir() -> None:
    assert _parse("--audio-dir", "/tmp/audio", "https://x").audio_dir == Path("/tmp/audio")


def test_url_positional() -> None:
    url = "https://www.bilibili.com/video/BV1xx411c7mD"
    assert _parse(url).url == url


# ─── main() with mocked dependencies ────────────────────────────────────────


def test_main_no_ytdlp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(downloader, "find_ytdlp", lambda: None)
    assert cli.main([]) == 1


def test_main_no_ffmpeg_warns(monkeypatch: pytest.MonkeyPatch, capsys: pytest.Capture) -> None:
    monkeypatch.setattr(downloader, "find_ytdlp", lambda: "yt-dlp")
    monkeypatch.setattr(ff, "find_ffmpeg", lambda: None)
    monkeypatch.setattr(cli, "_prepare_cookie", lambda opts: False)
    assert cli.main([]) == 1
    assert "ffmpeg" in capsys.readouterr().err


def test_main_non_interactive_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(downloader, "find_ytdlp", lambda: "yt-dlp")
    monkeypatch.setattr(ff, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(cli, "_prepare_cookie", lambda opts: True)
    monkeypatch.setattr(cli, "_run_once", lambda opts, url, ytdlp, ffmpeg_bin: True)
    assert cli.main(["https://bilibili.com/video/BV1"]) == 0


def test_main_non_interactive_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(downloader, "find_ytdlp", lambda: "yt-dlp")
    monkeypatch.setattr(ff, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(cli, "_prepare_cookie", lambda opts: True)
    monkeypatch.setattr(cli, "_run_once", lambda opts, url, ytdlp, ffmpeg_bin: False)
    assert cli.main(["https://bilibili.com/video/BV1"]) == 1


def test_main_repl_eof_exits_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """REPL should exit 0 on EOF (stdin closed / redirected)."""
    monkeypatch.setattr(downloader, "find_ytdlp", lambda: "yt-dlp")
    monkeypatch.setattr(ff, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(cli, "_prepare_cookie", lambda opts: True)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    def raise_eof(prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("bili_dl.ui.prompt", raise_eof)
    assert cli.main([]) == 0


# ─── Config file loading + merging ──────────────────────────────────────────


def test_merge_settings_cli_overrides_config() -> None:
    """CLI flags take precedence over config file values."""
    args = _build_parser().parse_args(["-a", "--proxy", "http://cli:8080"])
    cfg = settings.Settings(
        mode="v",
        proxy="http://config:7890",
        insecure=True,
        video_dir=Path("/cfg/v"),
    )
    opts = cli._merge_settings(args, cfg)
    assert opts.mode == "a"  # CLI overrides
    assert opts.proxy == "http://cli:8080"  # CLI overrides
    assert opts.insecure is True  # config value (CLI didn't set)
    assert opts.video_dir == Path("/cfg/v")  # config value


def test_merge_settings_config_provides_defaults() -> None:
    """Config provides defaults when CLI flags are absent."""
    args = _build_parser().parse_args([])
    cfg = settings.Settings(
        mode="a",
        proxy="http://config:7890",
        insecure=True,
        cookie_dir=Path("/cfg/cookies"),
    )
    opts = cli._merge_settings(args, cfg)
    assert opts.mode == "a"
    assert opts.proxy == "http://config:7890"
    assert opts.insecure is True
    assert opts.cookie_dir == Path("/cfg/cookies")


def test_merge_settings_no_config_no_cli() -> None:
    """Without config or CLI flags, built-in defaults apply."""
    args = _build_parser().parse_args([])
    opts = cli._merge_settings(args, settings.Settings())
    assert opts.mode == "all"
    assert opts.proxy == ""
    assert opts.insecure is False
    assert opts.cookie_dir is None


def test_main_loads_config_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """main() reads config.toml and applies its values."""
    config_file = tmp_path / "config.toml"
    config_file.write_text('mode = "a"\nproxy = "http://cfg:7890"\n', encoding="utf-8")

    monkeypatch.setattr(downloader, "find_ytdlp", lambda: "yt-dlp")
    monkeypatch.setattr(ff, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(cli, "_prepare_cookie", lambda opts: True)
    captured_opts: list[cli.Options] = []
    monkeypatch.setattr(
        cli,
        "_run_once",
        lambda opts, url, ytdlp, ffmpeg_bin: captured_opts.append(opts) or True,
    )
    cli.main(["--config", str(config_file), "https://bilibili.com/video/BV1"])
    assert captured_opts[0].mode == "a"
    assert captured_opts[0].proxy == "http://cfg:7890"


# ─── Batch download ─────────────────────────────────────────────────────────


def test_read_batch_urls(tmp_path: Path) -> None:
    f = tmp_path / "urls.txt"
    f.write_text(
        "# comment\n"
        "https://bilibili.com/video/BV1\n"
        "\n"
        "  https://bilibili.com/video/BV2  \n"
        "# another comment\n"
        "https://bilibili.com/video/BV3\n",
        encoding="utf-8",
    )
    urls = cli._read_batch_urls(f)
    assert urls == [
        "https://bilibili.com/video/BV1",
        "https://bilibili.com/video/BV2",
        "https://bilibili.com/video/BV3",
    ]


def test_read_batch_urls_empty(tmp_path: Path) -> None:
    f = tmp_path / "empty.txt"
    f.write_text("# only comments\n\n", encoding="utf-8")
    assert cli._read_batch_urls(f) == []


def test_main_batch_file_not_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(downloader, "find_ytdlp", lambda: "yt-dlp")
    monkeypatch.setattr(ff, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(cli, "_prepare_cookie", lambda opts: True)
    assert cli.main(["--batch-file", "/nonexistent/urls.txt"]) == 1


def test_main_batch_download(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.Capture
) -> None:
    batch_file = tmp_path / "urls.txt"
    batch_file.write_text(
        "https://bilibili.com/video/BV1\nhttps://bilibili.com/video/BV2\n", encoding="utf-8"
    )
    monkeypatch.setattr(downloader, "find_ytdlp", lambda: "yt-dlp")
    monkeypatch.setattr(ff, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(cli, "_prepare_cookie", lambda opts: True)
    monkeypatch.setattr(cli, "_run_once", lambda opts, url, ytdlp, ffmpeg_bin: True)
    result = cli.main(["--batch-file", str(batch_file)])
    assert result == 0
    err = capsys.readouterr().err
    assert "2 个链接" in err
    assert "全部完成" in err


def test_main_batch_partial_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.Capture
) -> None:
    batch_file = tmp_path / "urls.txt"
    batch_file.write_text(
        "https://bilibili.com/video/BV1\nhttps://bilibili.com/video/BV2\n", encoding="utf-8"
    )
    monkeypatch.setattr(downloader, "find_ytdlp", lambda: "yt-dlp")
    monkeypatch.setattr(ff, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(cli, "_prepare_cookie", lambda opts: True)

    call_count = [0]

    def fake_run(opts, url, ytdlp, ffmpeg_bin) -> bool:
        call_count[0] += 1
        return call_count[0] == 1  # first succeeds, second fails

    monkeypatch.setattr(cli, "_run_once", fake_run)
    result = cli.main(["--batch-file", str(batch_file)])
    assert result == 1
    err = capsys.readouterr().err
    assert "1 成功" in err
    assert "1 失败" in err


def test_main_batch_empty_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    batch_file = tmp_path / "urls.txt"
    batch_file.write_text("# only comments\n", encoding="utf-8")
    monkeypatch.setattr(downloader, "find_ytdlp", lambda: "yt-dlp")
    monkeypatch.setattr(ff, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(cli, "_prepare_cookie", lambda opts: True)
    assert cli.main(["--batch-file", str(batch_file)]) == 0


# ─── clig.dev compliance: stdin TTY + env vars + no-color ───────────────────


def test_main_non_tty_no_url_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-interactive stdin + no URL → error, not REPL (clig.dev §Interactivity)."""
    monkeypatch.setattr(downloader, "find_ytdlp", lambda: "yt-dlp")
    monkeypatch.setattr(ff, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(cli, "_prepare_cookie", lambda opts: True)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert cli.main([]) == 1


def test_main_tty_no_url_enters_repl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Interactive stdin (TTY) + no URL → REPL."""
    monkeypatch.setattr(downloader, "find_ytdlp", lambda: "yt-dlp")
    monkeypatch.setattr(ff, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(cli, "_prepare_cookie", lambda opts: True)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    def raise_eof(prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("bili_dl.ui.prompt", raise_eof)
    assert cli.main([]) == 0


def test_merge_proxy_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTPS_PROXY env var fills in proxy when CLI and config don't."""
    monkeypatch.setenv("HTTPS_PROXY", "http://env:9999")
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    args = _build_parser().parse_args([])
    opts = cli._merge_settings(args, settings.Settings())
    assert opts.proxy == "http://env:9999"


def test_merge_proxy_cli_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI --proxy takes precedence over env var."""
    monkeypatch.setenv("HTTPS_PROXY", "http://env:9999")
    args = _build_parser().parse_args(["--proxy", "http://cli:8080"])
    opts = cli._merge_settings(args, settings.Settings())
    assert opts.proxy == "http://cli:8080"


def test_merge_proxy_config_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Config file proxy takes precedence over env var."""
    monkeypatch.setenv("HTTPS_PROXY", "http://env:9999")
    args = _build_parser().parse_args([])
    cfg = settings.Settings(proxy="http://cfg:7890")
    opts = cli._merge_settings(args, cfg)
    assert opts.proxy == "http://cfg:7890"


def test_main_no_color_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """--no-color calls ui.disable_color()."""
    monkeypatch.setattr(downloader, "find_ytdlp", lambda: "yt-dlp")
    monkeypatch.setattr(ff, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(cli, "_prepare_cookie", lambda opts: True)
    called = [False]

    def fake_disable() -> None:
        called[0] = True

    monkeypatch.setattr("bili_dl.ui.disable_color", fake_disable)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("bili_dl.ui.prompt", lambda p: "q")
    cli.main(["--no-color"])
    assert called[0] is True


def test_help_has_examples_and_url() -> None:
    """--help text includes examples and issues link (clig.dev §Help)."""
    parser = _build_parser()
    help_text = parser.format_help()
    assert "examples:" in help_text.lower() or "example" in help_text.lower()
    assert "github.com/Echoziness/bili-dl" in help_text


def test_main_no_ffmpeg_warns_to_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.Capture
) -> None:
    """Warning messages go to stderr, not stdout (clig.dev: messaging to stderr)."""
    monkeypatch.setattr(downloader, "find_ytdlp", lambda: "yt-dlp")
    monkeypatch.setattr(ff, "find_ffmpeg", lambda: None)
    monkeypatch.setattr(cli, "_prepare_cookie", lambda opts: False)
    cli.main([])
    captured = capsys.readouterr()
    assert "ffmpeg" in captured.err  # stderr, not stdout
    assert "ffmpeg" not in captured.out
