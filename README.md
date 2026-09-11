# bili-dl

> Cross-platform Bilibili downloader — a thin, fast wrapper around `yt-dlp` + `ffmpeg`.

[![CI](https://github.com/Echoziness/bili-dl/actions/workflows/ci.yml/badge.svg)](https://github.com/Echoziness/bili-dl/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![PyPI](https://img.shields.io/pypi/v/bili-dl)](https://pypi.org/project/bili-dl/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Download Bilibili videos and audio at the best available quality (up to
1080p for non-premium accounts). Cross-platform, with built-in QR login,
automatic session renewal, and foobar2000-friendly audio output.

## Features

- **Cookie-safe** — drop any `.txt` cookie export next to the tool, and only
  the Bilibili-domain entries are extracted. Other-site cookies are never
  parsed, stored, or sent anywhere.
- **Cookie-verified** — probes Bilibili's `nav` API to confirm your session is
  actually logged in before downloading.
- **foobar2000-friendly audio** — every produced M4A goes through a zero-loss
  `ffmpeg -c copy` remux (`moov`-first + ISOM container). No re-encode, no
  quality loss, instant playback in picky players.
- **Built-in login** — scan with the Bilibili App; no browser-cookie export,
  browser-profile access, or separate feature installation.
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

#### Scan a QR code (recommended)

`bili-dl` can create a **separate Bilibili Web login session** by
scanning a QR code with the Bilibili App. It does not read your browser
profile, inspect browser cookies, or require a particular browser.

After the normal installation, simply run:

```bash
bili-dl login
```

The command displays a QR code in an interactive terminal. Scan and confirm
it in the Bilibili App. On success, it verifies the newly issued session with
Bilibili before atomically replacing `cookies_bilibili.txt`; your normal
download command can then reuse it. `bili-dl login` deliberately does not
need `yt-dlp` or `ffmpeg`, so it can be tested on its own.

On a successful QR login, `bili-dl` also saves Bilibili's refresh credential
and a one-way fingerprint of its Cookie session in a separate, Git-ignored
`auth_state.json` next to the cookie file. Before a download, it checks at
most once per UTC day whether Bilibili asks to renew the Web session. If
renewal is requested, the new Cookie is verified and saved before the old
refresh credential is confirmed as spent. A stale credential is never used
after a Cookie is replaced or manually imported. If confirmation is
temporarily unreachable, it is recorded and retried before a later renewal
check. This check never opens a QR prompt; a transient renewal error leaves a
currently valid Cookie usable and reports a warning instead.

This is **not** a promise of permanent login. Bilibili can still invalidate a
session for expiry, account security, or risk control. In that case, run
`bili-dl login` again. If you logged in with an earlier test build, run it
once more after upgrading so the session-bound refresh state can be stored.

#### Import an existing browser export

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
bili-dl --status                               # inspect login / renewal status
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
| `--proxy URL` | proxy for yt-dlp (env: `HTTPS_PROXY`/`HTTP_PROXY`) |
| `--no-color` | disable colored output (also: `NO_COLOR` env var) |
| `--config FILE` | override config file path |
| `--batch-file FILE` | download URLs listed in a text file |
| `--status` | show login, Bilibili's current renewal requirement, and automatic-renewal status; never downloads or refreshes |
| `-V`, `--version` | show version |
| `-h`, `--help` | show help |

## Privacy

- Only Bilibili-domain cookie lines are kept; all others are discarded in
  memory — never written to disk or sent anywhere.
- `bili-dl login` and its renewal check communicate only with Bilibili's
  login, session-check, and renewal endpoints; they do not access any browser
  profile or send data to a third party. Downloads contact the URLs you
  provide through `yt-dlp`. No telemetry or analytics.
- Browser-imported cookies are backed up before replacement. QR-login cookies
  replace the destination atomically, but only after an online Bilibili
  session check succeeds; a failed check leaves the prior file untouched.
- `auth_state.json` contains Bilibili refresh credentials and a one-way
  Cookie-session fingerprint, including an old credential temporarily kept
  only when server confirmation must be retried. It is therefore Git-ignored
  along with its atomic-write temporary file. It is stored beside the Cookie
  in the per-user config directory; on POSIX its mode is set to `0600`.

## Windows: Controlled Folder Access

Windows Defender's *Controlled Folder Access* (CFA) blocks unsigned apps from
writing to library folders (`~/Videos`, `~/Music`, etc.) by default. It fails
**silently** — ffmpeg may crash with a confusing `Could not write header ... No
such file or directory` while the file is never created.

If you enabled CFA and downloads fail after the video is written:

1. Open **Windows Security → Virus & threat protection → Ransomware
   protection → Manage ransomware protection**
2. Under *Controlled folder access*, click **Allow an app through controlled
   folder access → Add an allowed app**
3. Add `ffmpeg.exe` (and `yt-dlp.exe`) — use the **real path**, not a symlink:
   - ffmpeg (scoop): `%USERPROFILE%\scoop\apps\ffmpeg\<version>\bin\ffmpeg.exe`
   - yt-dlp: `%USERPROFILE%\miniforge3\Scripts\yt-dlp.exe` (or your install)

> **Tip**: adding the `current` junction path does **not** work — CFA matches
> the resolved real path (e.g. `...\ffmpeg\9.0.1\bin\ffmpeg.exe`).

## Limitations

- **Single video only** — `--no-playlist` is always passed, so multi-P
  videos, collections, and favourites are not downloaded as a batch. Give
  each part's URL separately (or list them in a `--batch-file`).
- **Batch downloads are sequential** — no concurrency. A long URL list takes
  proportionally longer; this keeps memory low and avoids hammering Bilibili.
- **Re-downloading overwrites** — no `--no-overwrites` / `--continue` is
  passed to yt-dlp. Running the same URL twice re-downloads and replaces the
  file.
- **Windows CJK filenames** — on a stock Windows console (cp936/GBK) titles
  containing rare characters or emoji may lose those characters in the saved
  filename. Common Chinese characters are unaffected. This is a deliberate
  trade-off for reliable path matching (see AGENTS.md §2.9); forcing UTF-8
  would silently break downloads instead.

## License

[MIT](LICENSE). `bili-dl` is a wrapper; the actual downloading is done by
[`yt-dlp`](https://github.com/yt-dlp/yt-dlp) (Unlicense) and
[`ffmpeg`](https://ffmpeg.org) (LGPL/GPL), which you must install separately.
