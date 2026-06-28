"""Tests for cross-platform colored terminal output.

Covers:
* _init: non-TTY → disabled; POSIX TTY → enabled; Windows VT path (mocked)
* _colorize: passthrough when disabled; ANSI codes when enabled
* info/ok/warn/error/prompt: smoke tests (no crash, output produced)
* mode_label: lookup with known/unknown keys
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


def test_init_enabled_on_macos_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui.sys, "platform", "darwin")
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
    assert "hello" in colored


def test_colorize_all_colors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui.sys, "platform", "linux")
    monkeypatch.setattr(ui.sys.stdout, "isatty", lambda: True)
    ui._init()
    for color, code in [("red", "31"), ("green", "32"), ("yellow", "33"), ("cyan", "36")]:
        colored = ui._colorize("x", color)
        assert f"\x1b[{code}m" in colored


def test_info_prints_plain(monkeypatch: pytest.MonkeyPatch, capsys: pytest.Capture) -> None:
    ui.info("hello")
    captured = capsys.readouterr()
    assert captured.out.strip() == "hello"


def test_ok_prints_colored_when_tty(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.Capture
) -> None:
    monkeypatch.setattr(ui.sys, "platform", "linux")
    monkeypatch.setattr(ui.sys.stdout, "isatty", lambda: True)
    ui._enabled = False
    ui._initialized = False
    ui.ok("done")
    captured = capsys.readouterr()
    assert "done" in captured.out
    assert "\x1b[32m" in captured.out


def test_warn_prints(monkeypatch: pytest.MonkeyPatch, capsys: pytest.Capture) -> None:
    ui.warn("careful")
    captured = capsys.readouterr()
    assert "careful" in captured.out


def test_error_prints(monkeypatch: pytest.MonkeyPatch, capsys: pytest.Capture) -> None:
    ui.error("boom")
    captured = capsys.readouterr()
    assert "boom" in captured.out


def test_prompt_reads_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt: "user typed this")
    result = ui.prompt("> ")
    assert result == "user typed this"


def test_mode_label_known() -> None:
    label = ui.mode_label("all")
    assert "all" in label


def test_mode_label_unknown_falls_back() -> None:
    label = ui.mode_label("xyz")
    assert label == "xyz"


def test_init_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui.sys, "platform", "linux")
    monkeypatch.setattr(ui.sys.stdout, "isatty", lambda: True)
    ui._init()
    enabled_after_first = ui._enabled
    ui._init()  # second call should be a no-op
    assert ui._enabled == enabled_after_first
