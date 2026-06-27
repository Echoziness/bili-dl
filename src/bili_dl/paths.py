"""Cross-platform path resolution for bili-dl.

No Windows-specific APIs (no Known Folders SHGetKnownFolderPath), no
外部依赖. Uses only stdlib + XDG conventions so the same code runs on
Windows / macOS / Linux without modification.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "bili-dl"


def _xdg(env_var: str, fallback: Path) -> Path:
    val = os.environ.get(env_var)
    if val:
        return Path(val).expanduser()
    return fallback


def config_dir() -> Path:
    """Per-user config / state directory (stores cookies_bilibili.txt etc.).

    Windows : %APPDATA%\\bili-dl        (roaming, survives reinstalls)
    macOS   : ~/Library/Application Support/bili-dl
    Linux   : $XDG_CONFIG_HOME/bili-dl  (fallback ~/.config/bili-dl)
    """
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return _xdg("XDG_CONFIG_HOME", Path.home() / ".config") / APP_NAME


def default_video_dir() -> Path:
    """Default directory for downloaded videos.

    Windows : ~/Videos/bilibili_videos   (Movie library folder)
    macOS   : ~/Movies/bilibili_videos
    Linux   : $XDG_DOWNLOAD_DIR/bilibili_videos (fallback ~/Downloads)
    """
    if sys.platform == "darwin":
        return Path.home() / "Movies" / "bilibili_videos"
    if sys.platform == "win32":
        return Path.home() / "Videos" / "bilibili_videos"
    return _xdg("XDG_DOWNLOAD_DIR", Path.home() / "Downloads") / "bilibili_videos"


def default_audio_dir() -> Path:
    """Default directory for downloaded/extracted audio (m4a).

    Windows : ~/Music/bilibili_audio
    macOS   : ~/Music/bilibili_audio
    Linux   : ~/.local/share/bili-dl/audio  (XDG_DATA_HOME fallback)
    """
    if sys.platform == "darwin":
        return Path.home() / "Music" / "bilibili_audio"
    if sys.platform == "win32":
        return Path.home() / "Music" / "bilibili_audio"
    return _xdg("XDG_DATA_HOME", Path.home() / ".local" / "share") / APP_NAME / "audio"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
