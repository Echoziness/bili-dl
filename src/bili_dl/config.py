"""Project-wide constants. No mutable state lives here."""

from __future__ import annotations

# Bilibili endpoints
NAV_API = "https://api.bilibili.com/x/web-interface/nav"
VIDEO_INFO_API = "https://api.bilibili.com/x/web-interface/view"
PLAYER_INFO_API = "https://api.bilibili.com/x/player/wbi/v2"
COMMENT_MAIN_API = "https://api.bilibili.com/x/v2/reply/wbi/main"
COMMENT_REPLY_API = "https://api.bilibili.com/x/v2/reply/reply"
REFERER = "https://www.bilibili.com"
QR_GENERATE_API = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
QR_POLL_API = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
COOKIE_INFO_API = "https://passport.bilibili.com/x/passport-login/web/cookie/info"
COOKIE_REFRESH_API = "https://passport.bilibili.com/x/passport-login/web/cookie/refresh"
COOKIE_CONFIRM_REFRESH_API = "https://passport.bilibili.com/x/passport-login/web/confirm/refresh"
CORRESPOND_URL_PREFIX = "https://www.bilibili.com/correspond/1/"

# Browser identity for Bilibili login and session APIs. The nav endpoint returns
# HTTP 412 to urllib's default "Python-urllib/x.y" UA. yt-dlp sends its own UA.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"

# Cookie filenames
# cookies_bilibili.txt is the extracted output (only .bilibili.com entries).
# Source files are auto-detected — any .txt file in the cookie directory
# containing bilibili entries will be recognised.
BILI_COOKIE_FILENAME = "cookies_bilibili.txt"

# User configuration file (TOML format, loaded by settings.py).
# Lives in the config directory alongside cookies_bilibili.txt.
CONFIG_FILENAME = "config.toml"

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
    "s": "s   仅字幕 (SRT)",
}

VALID_MODES = tuple(MODE_LABELS)

CONTENT_API_TIMEOUT = 15.0

# Comment downloads may span thousands of pages. Keep requests sequential and
# gently paced; retry only transient transport failures within the module.
COMMENT_PAGE_DELAY = 0.3
COMMENT_RETRY_DELAYS = (0.5, 1.0)

# Default timeout for Bilibili login/session API requests. The nav probe
# degrades to local validation; renewal failures preserve the current session.
AUTH_API_TIMEOUT = 5.0

# Web QR login limits.  The Bilibili-issued QR key itself expires after about
# 180 seconds; polling once a second matches the browser flow without making
# unnecessary requests.
QR_TIMEOUT = 10.0
QR_LOGIN_MAX_POLLS = 180
QR_POLL_INTERVAL = 1.0

# Web Cookie renewal endpoints use the same short, failure-tolerant network
# budget as the normal login probe.  A renewal check is throttled to once per
# UTC day, with an explicit re-login remaining the fallback for every error.
# The cross-process lock covers only login/renewal state changes; contention
# never blocks a download that already has a valid Cookie.
AUTH_STATE_FILENAME = "auth_state.json"
AUTH_LOCK_FILENAME = ".auth_state.lock"
AUTH_LOCK_TIMEOUT = 5.0

# Repair-AudioContainer: minimum ratio of (new size / original size) for the
# remuxed file to be considered successful. Guards against truncated output.
REPAIR_MIN_SIZE_RATIO = 0.5
