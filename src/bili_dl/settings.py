"""User configuration loading from TOML files.

Single responsibility: read a ``config.toml`` file and return a
:class:`Settings` object. Pure logic — no ``ui.*`` calls.

TOML is used because it is the Python ecosystem standard (PEP 518/621)
and ``tomllib`` is stdlib since Python 3.11, preserving the zero-dependency
constraint.

Config file schema (all fields optional)::

    mode = "all"          # "all" | "v" | "a"
    proxy = "http://..."
    insecure = false
    video_dir = "/path"
    audio_dir = "/path"
    cookie_dir = "/path"

CLI flags always override config file values — config provides defaults,
flags provide per-invocation overrides.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass
class Settings:
    """Parsed user configuration. Fields are ``None`` when unset in the file."""

    mode: Optional[str] = None
    proxy: Optional[str] = None
    insecure: Optional[bool] = None
    video_dir: Optional[Path] = None
    audio_dir: Optional[Path] = None
    cookie_dir: Optional[Path] = None


def load(path: Path) -> Settings:
    """Load and parse a TOML config file.

    Returns an empty :class:`Settings` (all fields ``None``) if the file
    doesn't exist or is empty. Raises ``tomllib.TOMLDecodeError`` on
    malformed TOML — the caller decides how to report it.

    ``insecure`` is type-checked: a non-bool value (e.g. ``"yes"``) is
    coerced to ``None`` so downstream ``cfg.insecure or False`` can't pick
    up a truthy string by accident.
    """
    if not path.exists():
        return Settings()

    with path.open("rb") as f:
        data: dict[str, Any] = tomllib.load(f)

    insecure_raw = data.get("insecure")
    insecure = insecure_raw if isinstance(insecure_raw, bool) else None

    return Settings(
        mode=data.get("mode"),
        proxy=data.get("proxy") or None,
        insecure=insecure,
        video_dir=Path(data["video_dir"]) if data.get("video_dir") else None,
        audio_dir=Path(data["audio_dir"]) if data.get("audio_dir") else None,
        cookie_dir=Path(data["cookie_dir"]) if data.get("cookie_dir") else None,
    )
