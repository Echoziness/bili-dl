"""Minimal cross-platform colored terminal output.

Zero-dependency. On Windows 10+ we enable ENABLE_VIRTUAL_TERMINAL_PROCESSING
so ANSI escape codes work in conhost/Windows Terminal; older Windows and all
POSIX terminals already support ANSI. Falls back to plain text if enabling
fails or when stdout is redirected (not a TTY).
"""

from __future__ import annotations

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


def _init() -> None:
    global _enabled, _initialized
    if _initialized:
        return
    _initialized = True
    if not sys.stdout.isatty():
        return
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
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
    print(text)


def ok(text: str) -> None:
    print(_colorize(text, "green"))


def warn(text: str) -> None:
    print(_colorize(text, "yellow"))


def error(text: str) -> None:
    print(_colorize(text, "red"))


def prompt(text: str) -> str:
    """Read one line from stdin. Plain input() — portable across platforms."""
    return input(text)


def mode_label(mode: str) -> str:
    """Localised mode label for prompts; '?' kept as it's just a marker."""
    from .config import MODE_LABELS

    return MODE_LABELS.get(mode, mode)
