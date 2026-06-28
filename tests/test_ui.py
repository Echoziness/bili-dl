"""Tests for cross-platform colored terminal output.

Covers:
* _init: non-TTY → disabled; POSIX TTY → enabled
* _colorize: passthrough when disabled; ANSI codes when enabled
* info/ok/prompt: smoke tests (no crash, output produced)
"""

from __future__ import annotations

import pytest

from bili_dl import ui


@pytest.fixture(autouse=True)
def _reset_ui_state() -> None:
    """Reset module-level _enabled/_initialized before each test."""
    ui._enabled = False
    ui._initialized = False


def test_init_disabled_when_not_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui.sys.stdout, "isatty", lambda: False)
    ui._init()
    assert ui._enabled is False


def test_init_enabled_on_posix_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui.sys, "platform", "linux")
    monkeypatch.setattr(ui.sys.stdout, "isatty", lambda: True)
    ui._init()
    assert ui._enabled is True


def test_colorize_passthrough_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui.sys.stdout, "isatty", lambda: False)
    ui._init()
    assert ui._colorize("hello", "red") == "hello"


def test_colorize_adds_ansi_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui.sys, "platform", "linux")
    monkeypatch.setattr(ui.sys.stdout, "isatty", lambda: True)
    ui._init()
    colored = ui._colorize("hello", "green")
    assert "\x1b[32m" in colored
    assert "\x1b[0m" in colored


def test_info_prints_plain(capsys: pytest.Capture) -> None:
    ui.info("hello")
    assert capsys.readouterr().out.strip() == "hello"


def test_ok_prints_colored_when_tty(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.Capture
) -> None:
    monkeypatch.setattr(ui.sys, "platform", "linux")
    monkeypatch.setattr(ui.sys.stdout, "isatty", lambda: True)
    ui._enabled = False
    ui._initialized = False
    ui.ok("done")
    out = capsys.readouterr().out
    assert "done" in out
    assert "\x1b[32m" in out


def test_prompt_reads_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt: "user typed this")
    assert ui.prompt("> ") == "user typed this"
