# bili-dl

> Cross-platform Bilibili downloader — a thin, fast wrapper around `yt-dlp` + `ffmpeg`.

[![CI](https://github.com/Echoziness/bili-dl/actions/workflows/ci.yml/badge.svg)](https://github.com/Echoziness/bili-dl/actions/workflows/ci.yml)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

`bili-dl` is a small command-line tool for downloading Bilibili videos and
audio with the best available quality (up to 1080p for non-premium accounts).
It started life as a Windows PowerShell script (`bd.ps1`) and was rewritten in
pure Python so the **same code runs unchanged on Windows, macOS and Linux**,
with **zero runtime dependencies** (standard library only).

It does one thing well: pick the right `yt-dlp` format, manage your Bilibili
cookie safely, and standardise every audio file into a foobar2000-friendly
container — all without re-encoding.

---

## Why

- **Cookie-safe.** Drop a full-browser `cookies_all.txt` export next to the
  tool and it extracts *only* the `.bilibili.com` entries into a dedicated
  file. Cookies for every other site are never parsed, stored, or sent
  anywhere.
- **Cookie-verified.** Before downloading it pokes the Bilibili `nav` API to
  confirm your session is actually logged in, falling back to a local check
  when the network is unavailable.
- **Audio you can actually play.** Both the "audio only" download and the
  audio extracted from a video are routed through `ffmpeg -c:a copy
  -movflags +faststart` — a zero-loss remux that produces a `moov`-first
  ISOM container. foobar2000 and friends play it instantly without bitrate
  quirks from Bilibili's raw mux.
- **No quality downgrade bugs.** A scoping bug in the original PowerShell
  version meant the cookie/referer args were silently dropped inside the
  download function, quietly capping quality. The rewrite passes all options
  explicitly, so this class of bug can't recur.
- **Zero install footprint beyond the tools you already have.** No `pip`
  dependencies, no `requests`, no `colorama` — just the standard library. If
  you have Python installed, `pipx install bili-dl` is enough.

---

## Install

`bili-dl` needs `yt-dlp` and `ffmpeg` on your `PATH`. Both are one-liners:

```bash
pip install -U yt-dlp      # or: winget install yt-dlp / brew install yt-dlp / pipx install yt-dlp
# ffmpeg:
winget install ffmpeg       # Windows
brew install ffmpeg         # macOS
sudo apt install ffmpeg     # Debian/Ubuntu
```

Then install bili-dl itself:

```bash
pipx install bili-dl        # recommended: isolated, exposes the `bili-dl` command
# or, if you must:
pip install bili-dl
```

Verify:

```bash
bili-dl --help
bili-dl -V          # show version
```

---

## Quick start

### Cookies (one-time)

Bilibili returns HTTP 412 to anonymous requests, so you need a login cookie.
On Windows `yt-dlp --cookies-from-browser` can't read Chromium's App-Bound
Encryption, so export manually:

1. Install the [Get cookies.txt LOCALLY](https://microsoftedge.microsoft.com/addons/detail/get-cookies-txt-locally/ccpbcjjkcbiojbicneopklbjmhklbpca) Edge/Chrome extension.
2. Log in to [bilibili.com](https://www.bilibili.com).
3. Export **All Cookies** in Netscape format.
4. Save as `cookies_all.txt` in the cookie directory (see below).
5. Run `bili-dl` once — it extracts only the `.bilibili.com` entries into
   `cookies_bilibili.txt` and reuses it thereafter.

Cookie directory defaults to:

| OS | Path |
|---|---|
| Windows | `%APPDATA%\bili-dl` |
| macOS | `~/Library/Application Support/bili-dl` |
| Linux | `~/.config/bili-dl` |

Override with `--cookie-dir`.

### Download

```bash
# interactive REPL — paste URLs, switch modes with all/v/a, quit with q
bili-dl

# one-shot
bili-dl https://www.bilibili.com/video/BVxxxxxxxx

# audio only → standalone, foobar2000-friendly M4A
bili-dl -a https://www.bilibili.com/video/BVxxxxxxxx

# video only (single-file MP4 when available)
bili-dl -v https://www.bilibili.com/video/BVxxxxxxxx

# through a proxy, skipping TLS verification for a self-signed environment
bili-dl --proxy http://127.0.0.1:7890 -k https://www.bilibili.com/video/BVxxxxxxxx
```

Download destinations default to:

| OS | Videos | Audio |
|---|---|---|
| Windows | `~/Videos/bilibili_videos` | `~/Music/bilibili_audio` |
| macOS | `~/Movies/bilibili_videos` | `~/Music/bilibili_audio` |
| Linux | `~/Downloads/bilibili_videos` | `~/.local/share/bili-dl/audio` |

Override with `--output-dir` / `--audio-dir`.

---

## Modes

| Mode | Flag | What it downloads | Container |
|---|---|---|---|
| **all** | `--all` (default) | best video + best audio, merged | MP4 + extracted M4A |
| **video** | `-v` | video (single-file MP4 if a combined stream exists) | MP4 |
| **audio** | `-a` | best audio stream | M4A (faststart ISOM) |

In `all` mode the merged MP4 is kept in the videos directory **and** a
zero-loss M4A is extracted to the audio directory. Both audio paths (direct
download *and* extraction) go through the same ffmpeg remux, so they're
byte-layout-identical.

---

## How audio standardisation works

Bilibili serves DASH audio with `moov` at the tail and an `M4A` major brand.
Some players (notably foobar2000) read bitrate metadata incorrectly or show
a noticeable start delay. `bili-dl` runs:

```
ffmpeg -i in.m4a -map 0:a -c:a copy -map_metadata 0 -movflags +faststart out.m4a
```

`-c:a copy` means **no re-encode** — the AAC frames are copied verbatim, so
bitrate (e.g. 201 kbps for the 203k stream) is preserved exactly. Only the
container layout changes: `moov` moves to the front for instant playback, and
the compatible brand set becomes `M4A isom iso2`. Net effect: same audio,
friendlier file.

---

## Privacy

- The cookie importer reads `cookies_all.txt` line by line and keeps **only**
  lines containing `bilibili`. Lines for other domains are discarded in
  memory; they're never written to disk or sent over the network.
- The only network requests `bili-dl` makes go to `api.bilibili.com` (the
  login probe) and the URLs you give it (via `yt-dlp`). No telemetry, no
  analytics, no "phone home".
- `cookies_bilibili.txt` is created with restrictive handling and a timestamped
  backup is kept before any overwrite.

---

## Project layout

```
bili-dl/
├── src/bili_dl/
│   ├── __init__.py        # version
│   ├── __main__.py        # python -m bili_dl
│   ├── cli.py             # argparse + REPL entry point
│   ├── config.py          # constants (endpoints, format strings, labels)
│   ├── paths.py           # cross-platform path resolution (Win/mac/Linux)
│   ├── cookies.py         # Netscape extraction + (online) validity check
│   ├── ffmpeg.py          # ffmpeg discovery + zero-loss audio remux/extract
│   ├── downloader.py      # yt-dlp two-phase download (predict + fetch)
│   └── ui.py              # ANSI-colored output (enables VT on Windows)
├── tests/                 # pytest: cookies, paths, package smoke
├── pyproject.toml         # hatchling build, ruff, pytest config
└── .github/workflows/ci.yml
```

---

## Contributing

`bili-dl` is MIT-licensed and welcomes contributions. The project uses
`ruff` for lint/format and `pytest` for tests. Before sending a PR:

```bash
pip install -e . pytest ruff
ruff check src tests
ruff format --check src tests
pytest
```

CI runs the same checks on Windows, macOS and Linux across Python 3.9–3.13.

---

## License

[MIT](LICENSE). Note that `bili-dl` is a *wrapper*; the actual downloading is
done by [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) (Unlicense) and
[`ffmpeg`](https://ffmpeg.org) (LGPL/GPL). You must install those separately,
and any obligations imposed by their licenses apply to those binaries — not
to this wrapper's MIT license.