"""Shared input and output contracts for media and subtitle downloads."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config import REFERER


@dataclass
class DownloadConfig:
    """One resolved download request; subtitle output shares video_dir."""

    mode: str
    video_dir: Path
    audio_dir: Path
    cookie_path: Path
    proxy: str = ""
    insecure: bool = False
    ytdlp: Optional[tuple[str, ...]] = None
    ffmpeg_bin: Optional[str] = None
    referer: str = REFERER
    stage: Optional[Callable[[str, Optional[Path]], None]] = None
    no_color: bool = False


@dataclass
class DownloadResult:
    """Structured result consumed by the CLI, batch runner and REPL."""

    success: bool
    messages: list[tuple[str, str]] = field(default_factory=list)
    output_path: Optional[Path] = None
    additional_paths: list[Path] = field(default_factory=list)
