# bili-dl

> Cross-platform Bilibili downloader — a thin, fast wrapper around `yt-dlp` + `ffmpeg`.

[![CI](https://github.com/Echoziness/bili-dl/actions/workflows/ci.yml/badge.svg)](https://github.com/Echoziness/bili-dl/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![PyPI](https://img.shields.io/pypi/v/bili-dl)](https://pypi.org/project/bili-dl/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Download Bilibili videos and audio at the best available quality (up to
1080p for non-premium accounts). Cross-platform, zero runtime dependencies,
foobar2000-friendly audio output.

## Features

- **Cookie-safe** — drop any `.txt` cookie export next to the tool, and only
  the Bilibili-domain entries are extracted. Other-site cookies are never
  parsed, stored, or sent anywhere.
- **Cookie-verified** — probes Bilibili's `nav` API to confirm your session is
  actually logged in before downloading.
- **foobar2000-friendly audio** — every produced M4A goes through a zero-loss
  `ffmpeg -c copy` remux (`moov`-first + ISOM container). No re-encode, no
  quality loss, instant playback in picky players.
- **Zero runtime deps** — standard library only. If you have Python, `pipx
  install bili-dl` is all you need.
- **Configurable** — set defaults in a TOML config file (`mode`, `proxy`,
  output dirs, etc.); CLI flags override per-invocation.
- **Batch download** — download a list of URLs from a text file.

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

Bilibili requires a login cookie. Use any browser extension that exports
cookies in **Netscape format** (Cookie-Editor, Get cookies.txt, etc.):

1. Log in to [bilibili.com](https://www.bilibili.com)
2. Export cookies — the file should look like:

   ```
   # Netscape HTTP Cookie File
   .bilibili.com	TRUE	/	FALSE	0	SESSDATA	<session>
   .bilibili.com	TRUE	/	FALSE	0	bili_jct	<csrf>
   ```

3. Save the exported `.txt` file in the cookie directory (any filename works):

| OS | Cookie directory |
|---|---|
| Windows | `%APPDATA%\bili-dl` |
| macOS | `~/Library/Application Support/bili-dl` |
| Linux | `~/.config/bili-dl` |

Run `bili-dl` once — it auto-detects any `.txt` file containing Bilibili
entries, extracts only those, and reuses them thereafter. Override with
`--cookie-dir`.

### Download

```bash
bili-dl                                       # interactive REPL
bili-dl https://www.bilibili.com/video/BV...   # one-shot (video + audio)
bili-dl -a https://www.bilibili.com/video/BV...  # audio only
bili-dl -v https://www.bilibili.com/video/BV...  # video only
bili-dl --batch-file urls.txt                  # batch: download all URLs in file
```

| OS | Videos | Audio |
|---|---|---|
| Windows | `~/Videos/bilibili_videos` | `~/Music/bilibili_audio` |
| macOS | `~/Movies/bilibili_videos` | `~/Music/bilibili_audio` |
| Linux | `~/Downloads/bilibili_videos` | `~/Downloads/bilibili_audio` |

Override with `--output-dir` / `--audio-dir`.

### Config file

Save defaults in `config.toml` (in the cookie directory shown above) so you
don't repeat CLI flags every time:

```toml
mode = "a"                      # "all" | "v" | "a"
proxy = "http://127.0.0.1:7890"
insecure = false
video_dir = "/path/to/videos"
audio_dir = "/path/to/audio"
cookie_dir = "/path/to/cookies"
```

All fields are optional — set only what you need. CLI flags always override
config file values. Override the config path with `--config FILE`.

### Batch download

Create a text file with one URL per line (`#` for comments):

```text
# my playlist
https://www.bilibili.com/video/BV1xx...
https://www.bilibili.com/video/BV2xx...
```

```bash
bili-dl --batch-file urls.txt
```

### CLI reference

| Flag | Description |
|------|-------------|
| `--all` | video + audio, merged MP4 + extracted M4A (default) |
| `-v`, `--video` | video only (MP4) |
| `-a`, `--audio` | audio only (M4A, faststart ISOM) |
| `-k`, `--insecure` | skip TLS certificate verification |
| `--proxy URL` | proxy for yt-dlp |
| `--config FILE` | override config file path |
| `--batch-file FILE` | download URLs listed in a text file |
| `-V`, `--version` | show version |
| `-h`, `--help` | show help |

## Privacy

- Only Bilibili-domain cookie lines are kept; all others are discarded in
  memory — never written to disk or sent anywhere.
- Network requests go only to `api.bilibili.com` (login probe) and the URLs
  you provide (via `yt-dlp`). No telemetry, no analytics.
- `cookies_bilibili.txt` is created with a timestamped backup before any
  overwrite.

## License

[MIT](LICENSE). `bili-dl` is a wrapper; the actual downloading is done by
[`yt-dlp`](https://github.com/yt-dlp/yt-dlp) (Unlicense) and
[`ffmpeg`](https://ffmpeg.org) (LGPL/GPL), which you must install separately.