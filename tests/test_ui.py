"""Tests for cross-platform colored terminal output.

Covers:
* _init: non-TTY → disabled; POSIX TTY → enabled; NO_COLOR → disabled; TERM=dumb → disabled
* _colorize: passthrough when disabled; ANSI codes when enabled
* disable_color(): explicit --no-color flag
* info/ok/prompt: smoke tests (output goes to stderr)
"""

from __future__ import annotations

import pytest

from bili_dl import ui


@pytest.fixture(autouse=True)
def _reset_ui_state() -> None:
    """Reset module-level state before each test."""
    ui._enabled = False
    ui._initialized = False
    ui._user_disabled = False


def test_init_disabled_when_not_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui.sys.stderr, "isatty", lambda: False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("TERM", raising=False)
    ui._init()
    assert ui._enabled is False


def test_init_enabled_on_posix_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui.sys, "platform", "linux")
    monkeypatch.setattr(ui.sys.stderr, "isatty", lambda: True)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("TERM", raising=False)
    ui._init()
    assert ui._enabled is True


def test_init_disabled_by_no_color_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui.sys, "platform", "linux")
    monkeypatch.setattr(ui.sys.stderr, "isatty", lambda: True)
    monkeypatch.setenv("NO_COLOR", "1")
    ui._init()
    assert ui._enabled is False


def test_init_disabled_by_term_dumb(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui.sys, "platform", "linux")
    monkeypatch.setattr(ui.sys.stderr, "isatty", lambda: True)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "dumb")
    ui._init()
    assert ui._enabled is False


def test_disable_color() -> None:
    ui.disable_color()
    assert ui._enabled is False
    assert ui._colorize("hello", "red") == "hello"


def test_colorize_passthrough_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui.sys.stderr, "isatty", lambda: False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    ui._init()
    assert ui._colorize("hello", "red") == "hello"


def test_colorize_adds_ansi_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui.sys, "platform", "linux")
    monkeypatch.setattr(ui.sys.stderr, "isatty", lambda: True)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("TERM", raising=False)
    ui._enabled = False
    ui._initialized = False
    colored = ui._colorize("hello", "green")
    assert "\x1b[32m" in colored
    assert "\x1b[0m" in colored


def test_info_prints_to_stderr(capsys: pytest.Capture) -> None:
    ui.info("hello")
    captured = capsys.readouterr()
    assert captured.out == ""  # stdout is clean (clig.dev: messaging to stderr)
    assert "hello" in captured.err


def test_ok_prints_to_stderr(monkeypatch: pytest.MonkeyPatch, capsys: pytest.Capture) -> None:
    ui.ok("done")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "done" in captured.err


def test_prompt_reads_stdin(monkeypatch: pytest.MonkeyPatch, capsys: pytest.Capture) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt: "user typed this")
    assert ui.prompt("> ") == "user typed this"
    captured = capsys.readouterr()
    # Prompt text must go to stderr, not stdout (clig.dev §Output) — keeps
    # `bili-dl | grep` clean even in the REPL.
    assert captured.out == ""
    assert "> " in captured.err


# ─── warn / error / mode_label ───────────────────────────────────────────────


def test_warn_prints_to_stderr(monkeypatch: pytest.MonkeyPatch, capsys: pytest.Capture) -> None:
    ui.warn("careful")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "careful" in captured.err


def test_error_prints_to_stderr(monkeypatch: pytest.MonkeyPatch, capsys: pytest.Capture) -> None:
    ui.error("broke")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "broke" in captured.err


def test_mode_label_known() -> None:
    assert "视频" in ui.mode_label("all")
    assert "音频" in ui.mode_label("a")


def test_mode_label_unknown_falls_back() -> None:
    assert ui.mode_label("bogus") == "bogus"


# ─── colorize: other colors + disabled-after-disable_color ──────────────────


def test_colorize_yellow_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui.sys, "platform", "linux")
    monkeypatch.setattr(ui.sys.stderr, "isatty", lambda: True)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("TERM", raising=False)
    ui._enabled = False
    ui._initialized = False
    assert "\x1b[33m" in ui._colorize("w", "yellow")


def test_colorize_cyan_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui.sys, "platform", "linux")
    monkeypatch.setattr(ui.sys.stderr, "isatty", lambda: True)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("TERM", raising=False)
    ui._enabled = False
    ui._initialized = False
    assert "\x1b[36m" in ui._colorize("c", "cyan")


def test_disable_color_wins_over_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """disable_color() before _init() keeps color off even on a TTY."""
    monkeypatch.setattr(ui.sys, "platform", "linux")
    monkeypatch.setattr(ui.sys.stderr, "isatty", lambda: True)
    monkeypatch.delenv("NO_COLOR", raising=False)
    ui.disable_color()
    ui._init()
    assert ui._enabled is False
    assert ui._colorize("x", "red") == "x"


# ─── Windows VT processing branch (exercised on any OS via ctypes mock) ──────


def test_init_windows_vt_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """On win32 with a TTY, enabling VT processing sets _enabled=True."""
    import ctypes

    class FakeKernel32:
        def GetStdHandle(self, h: int) -> int:
            return 999

        def GetConsoleMode(self, handle: int, ref: object) -> int:
            return 1  # success

        def SetConsoleMode(self, handle: int, mode: int) -> int:
            return 1

    class FakeWindll:
        kernel32 = FakeKernel32()

    monkeypatch.setattr(ui.sys, "platform", "win32")
    monkeypatch.setattr(ui.sys.stderr, "isatty", lambda: True)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("TERM", raising=False)
    monkeypatch.setattr(ctypes, "windll", FakeWindll(), raising=False)
    ui._enabled = False
    ui._initialized = False
    ui._init()
    assert ui._enabled is True


def test_init_windows_vt_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the Win32 console API raises, color is disabled (except branch)."""
    import ctypes

    class FakeKernel32:
        @staticmethod
        def GetStdHandle(h: int) -> int:
            raise OSError("no console")

    class FakeWindll:
        kernel32 = FakeKernel32()

    monkeypatch.setattr(ui.sys, "platform", "win32")
    monkeypatch.setattr(ui.sys.stderr, "isatty", lambda: True)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("TERM", raising=False)
    monkeypatch.setattr(ctypes, "windll", FakeWindll(), raising=False)
    ui._enabled = False
    ui._initialized = False
    ui._init()
    assert ui._enabled is False
