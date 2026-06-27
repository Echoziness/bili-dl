"""Project-wide constants. No mutable state lives here."""

from __future__ import annotations

# Bilibili endpoints
NAV_API = "https://api.bilibili.com/x/web-interface/nav"
REFERER = "https://www.bilibili.com"

# User-Agent for the nav validity probe. Bilibili returns HTTP 412 to
# urllib's default "Python-urllib/x.y" UA — must masquerade as a browser.
# Used only by the cookie validity check; yt-dlp sends its own UA when downloading.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"

# Cookie filenames
# cookies_bilibili.txt is the extracted output (only .bilibili.com entries).
# Source files are auto-detected — any .txt file in the cookie directory
# containing bilibili entries will be recognised.
BILI_COOKIE_FILENAME = "cookies_bilibili.txt"

# yt-dlp format selectors
#   all / v : "b/bv+ba/b"  —— best progressive OR best DASH video+audio
#   a       : "ba[ext=m4a]/ba"  —— prefer m4a audio stream
FMT_AV = "b/bv+ba/b"
FMT_AUDIO = "ba[ext=m4a]/ba"

# Mode labels (kept short; reused by CLI help and REPL prompt)
MODE_LABELS = {
    "all": "all 视频+音频",
    "v": "v   仅视频",
    "a": "a   仅音频",
}

VALID_MODES = ("all", "v", "a")

# HTTP timeout for the nav validity probe (seconds). 5s matches the original
# bd.ps1; on flaky networks we gracefully degrade to local-only validation.
NAV_TIMEOUT = 5.0

# Repair-AudioContainer: minimum ratio of (new size / original size) for the
# remuxed file to be considered successful. Guards against truncated output.
REPAIR_MIN_SIZE_RATIO = 0.5
