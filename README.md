# bili-dl

> Cross-platform Bilibili downloader — a thin, fast wrapper around `yt-dlp` + `ffmpeg`.

[![CI](https://github.com/Echoziness/bili-dl/actions/workflows/ci.yml/badge.svg)](https://github.com/Echoziness/bili-dl/actions/workflows/ci.yml)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![PyPI](https://img.shields.io/pypi/v/bili-dl)](https://pypi.org/project/bili-dl/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Download Bilibili videos and audio at the best available quality (up to
1080p for non-premium accounts). Cross-platform, zero runtime dependencies,
foobar2000-friendly audio output.

## Features

- **Cookie-safe** — drop `cookies_all.txt` next to the tool, and only the
  `.bilibili.com` entries are extracted. Other-site cookies are never parsed,
  stored, or sent anywhere.
- **Cookie-verified** — probes Bilibili's `nav` API to confirm your session is
  actually logged in before downloading.
- **foobar2000-friendly audio** — every produced M4A goes through a zero-loss
  `ffmpeg -c copy` remux (`moov`-first + ISOM container). No re-encode, no
  quality loss, instant playback in picky players.
- **Zero runtime deps** — standard library only. If you have Python, `pipx
  install bili-dl` is all you need.

## Install

`bili-dl` needs `yt-dlp` and `ffmpeg` on your `PATH`:

```bash
pip install -U yt-dlp       # or: winget / brew / pipx
# ffmpeg:
winget install ffmpeg        # Windows
brew install ffmpeg          # macOS
sudo apt install ffmpeg      # Debian/Ubuntu
```

Then:

```bash
pipx install bili-dl         # recommended
bili-dl -V                   # verify
```

## Quick start

### Cookies (one-time)

Bilibili requires a login cookie. Export from your browser:

1. Install [Get cookies.txt LOCALLY](https://microsoftedge.microsoft.com/addons/detail/get-cookies-txt-locally/ccpbcjjkcbiojbicneopklbjmhklbpca) (Edge/Chrome)
2. Log in to [bilibili.com](https://www.bilibili.com)
3. Export **All Cookies** in Netscape format
4. Save as `cookies_all.txt` in the cookie directory:

| OS | Cookie directory |
|---|---|
| Windows | `%APPDATA%\bili-dl` |
| macOS | `~/Library/Application Support/bili-dl` |
| Linux | `~/.config/bili-dl` |

Run `bili-dl` once — it extracts only `.bilibili.com` entries and reuses them
thereafter. Override with `--cookie-dir`.

### Download

```bash
bili-dl                                       # interactive REPL
bili-dl https://www.bilibili.com/video/BV...   # one-shot (video + audio)
bili-dl -a https://www.bilibili.com/video/BV...  # audio only
bili-dl -v https://www.bilibili.com/video/BV...  # video only
```

| OS | Videos | Audio |
|---|---|---|
| Windows | `~/Videos/bilibili_videos` | `~/Music/bilibili_audio` |
| macOS | `~/Movies/bilibili_videos` | `~/Music/bilibili_audio` |
| Linux | `~/Downloads/bilibili_videos` | `~/.local/share/bili-dl/audio` |

Override with `--output-dir` / `--audio-dir`.

### CLI reference

| Flag | Description |
|------|-------------|
| `--all` | video + audio, merged MP4 + extracted M4A (default) |
| `-v`, `--video` | video only (MP4) |
| `-a`, `--audio` | audio only (M4A, faststart ISOM) |
| `-k`, `--insecure` | skip TLS certificate verification |
| `--proxy URL` | proxy for yt-dlp |
| `-V`, `--version` | show version |
| `-h`, `--help` | show help |

## Privacy

- Only `.bilibili.com` cookie lines are kept; all others are discarded in
  memory — never written to disk or sent anywhere.
- Network requests go only to `api.bilibili.com` (login probe) and the URLs
  you provide (via `yt-dlp`). No telemetry, no analytics.
- `cookies_bilibili.txt` is created with a timestamped backup before any
  overwrite.

## License

[MIT](LICENSE). `bili-dl` is a wrapper; the actual downloading is done by
[`yt-dlp`](https://github.com/yt-dlp/yt-dlp) (Unlicense) and
[`ffmpeg`](https://ffmpeg.org) (LGPL/GPL), which you must install separately.