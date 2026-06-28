"""Minimal cross-platform colored terminal output.

Zero-dependency. On Windows 10+ we enable ENABLE_VIRTUAL_TERMINAL_PROCESSING
so ANSI escape codes work in conhost/Windows Terminal; older Windows and all
POSIX terminals already support ANSI. Falls back to plain text when:
- stdout is not a TTY (piped/redirected)
- ``NO_COLOR`` env var is set and non-empty (no-color.org standard)
- ``TERM`` is ``dumb``
- ``--no-color`` was passed (set via :func:`disable_color`)

All output goes to **stderr** — the terminal messages are never the primary
output (files on disk are). This keeps ``bili-dl URL | grep`` clean.
(clig.dev: "Send messaging to stderr.")
"""

from __future__ import annotations

import os
import sys
from typing import Final

# ANSI SGR codes — only what we use
_RESET: Final = "\x1b[0m"
_COLORS: Final = {
    "red": "\x1b[31m",
    "green": "\x1b[32m",
    "yellow": "\x1b[33m",
    "cyan": "\x1b[36m",
}

_enabled = False
_initialized = False
_user_disabled = False


def disable_color() -> None:
    """Explicitly disable color (e.g. via ``--no-color`` flag)."""
    global _user_disabled, _enabled, _initialized
    _user_disabled = True
    _enabled = False
    _initialized = True


def _init() -> None:
    global _enabled, _initialized
    if _initialized:
        return
    _initialized = True
    # User explicitly disabled via --no-color
    if _user_disabled:
        return
    # no-color.org standard: NO_COLOR set (any non-empty value) disables color
    if os.environ.get("NO_COLOR"):
        return
    # TERM=dumb means the terminal can't do ANSI
    if os.environ.get("TERM") == "dumb":
        return
    # Check stderr TTY (we write to stderr, not stdout)
    if not sys.stderr.isatty():
        return
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined, unused-ignore]
            handle = kernel32.GetStdHandle(-12)  # STD_ERROR_HANDLE
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
                _enabled = True
        except Exception:
            _enabled = False
    else:
        _enabled = True


def _colorize(text: str, color: str) -> str:
    _init()
    if not _enabled:
        return text
    return f"{_COLORS[color]}{text}{_RESET}"


def info(text: str) -> None:
    print(text, file=sys.stderr)


def ok(text: str) -> None:
    print(_colorize(text, "green"), file=sys.stderr)


def warn(text: str) -> None:
    print(_colorize(text, "yellow"), file=sys.stderr)


def error(text: str) -> None:
    print(_colorize(text, "red"), file=sys.stderr)


def prompt(text: str) -> str:
    """Read one line from stdin. Plain input() — portable across platforms."""
    return input(text)


def mode_label(mode: str) -> str:
    """Localised mode label for prompts; '?' kept as it's just a marker."""
    from .config import MODE_LABELS

    return MODE_LABELS.get(mode, mode)
