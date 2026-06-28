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


def test_prompt_reads_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt: "user typed this")
    assert ui.prompt("> ") == "user typed this"
