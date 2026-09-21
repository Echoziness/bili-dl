# bili-dl

> Cross-platform Bilibili video, audio, subtitle and comment downloader.

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
- **Timestamped subtitles** — `-s` saves the first subtitle track returned by
  Bilibili as a UTF-8 SRT file, with each cue's start and end timestamps. It reuses
  your login and proxy without downloading media or requiring yt-dlp/ffmpeg.
- **Structured comments** — download all API-visible main comments or one complete
  reply thread as UTF-8 JSON. Records keep only what reading comprehension needs
  (author, text, reply tree, counts, timestamps) — about 5% of the raw response;
  `--full` keeps the raw objects. Optional limits stop after an exact number of
  unique comments; no browser, yt-dlp or ffmpeg is required.

## Install

Video/audio downloads use `yt-dlp` and `ffmpeg` on your `PATH`. Subtitle/comment
downloads and login work with just `bili-dl` installed:

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

Login and renewal updates use a cross-process lock. If another `bili-dl`
process is already updating the session, a download keeps using its valid
Cookie and skips only that invocation's renewal attempt.

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
bili-dl -s https://www.bilibili.com/video/BV...  # subtitle only (SRT)
bili-dl --batch-file urls.txt                  # batch: download all URLs in file
bili-dl --status                               # inspect login / renewal status
bili-dl comments BV... --limit 100             # first 100 main comments
bili-dl replies BV... ROOT_ID                  # one complete reply thread
```

| OS | Videos | Audio |
|---|---|---|
| Windows | `~/Videos/bilibili_videos` | `~/Music/bilibili_audio` |
| macOS | `~/Movies/bilibili_videos` | `~/Music/bilibili_audio` |
| Linux | `~/Downloads/bilibili_videos` | `~/Downloads/bilibili_audio` |

Override with `--output-dir` / `--audio-dir`.

### Subtitles

```bash
bili-dl -s "https://www.bilibili.com/video/BV1Got26ZE5K/"
bili-dl --subtitle "https://www.bilibili.com/video/BV.../?p=2" --output-dir ./subtitles
bili-dl -s --batch-file urls.txt --output-dir ./subtitles
```

Subtitle mode uses the same login, automatic session renewal, proxy and config
as media downloads. In the interactive REPL, enter `s` to select it; `all`, `v`
and `a` remain available. Use `mode = "s"` in the config for a persistent default.

Only the **first track in Bilibili's returned list** is fetched, in server order.
There is no language preference, sorting, automatic transcription, or fallback
to a later track if the first one fails. A `?p=N` URL selects that part; otherwise
part 1 is used. Regular BV/AV video URLs, bare BV/AV identifiers and `b23.tv`
short links are supported. Bangumi/course URLs are not supported in this mode.

SRT output goes into the video output directory (`--output-dir` / `video_dir`),
named `title [BV…] p1.ai-zh.srt`, for example. Each cue retains its start and end
times with millisecond precision. No video or audio file is downloaded. A missing
subtitle or a failed request returns a failure status; an existing SRT is only
replaced after the new file is fully written. Subtitle requests always verify
TLS certificates; `-k` applies only to yt-dlp media downloads.

### Comments

```bash
# All API-visible main comments, newest first (default)
bili-dl comments "https://www.bilibili.com/video/BV.../"

# At most the first 100 main comments
bili-dl comments BV... --limit 100

# Bilibili's hot order (session-bound and always fetched sequentially)
bili-dl comments BV... --sort hot --limit 100

# One root comment and all of its child replies
bili-dl replies BV... 317745878352

# At most 50 objects in the thread, including the root comment itself
bili-dl replies BV... 317745878352 --limit 50
```

Both commands reuse the normal login, automatic renewal, proxy, `cookie_dir`
and `video_dir` settings. `--output-dir` overrides the destination. They do not
invoke yt-dlp or ffmpeg. Regular BV/AV URLs, bare identifiers and `b23.tv`
short links are supported.

Main comments default to `newest`. Pinned comments come first and count toward
the limit; subsequent comments follow the selected server order. Observed hot
pages can return an unchanged offset while their contents advance. Its server-side
mechanism is not confirmed, so hot pages are fetched sequentially in one session
and cannot be resumed from the offset alone. A transport failure in hot mode stops
the download: replaying an ambiguously completed request could skip a page.
Newest and numbered thread requests retry transient network/429/5xx failures
within a fixed budget. A rejected WBI signature refreshes its keys once per page.

Output is an atomic UTF-8 JSON document containing video metadata, the request,
one minified comment record per line, and a final result summary (`schema_version: 2`).
By default each record keeps only what reading comprehension needs: reply-tree IDs
(`rpid_str`/`root_str`/`parent_str`), epoch `ctime` plus a local-timezone readable
`time`, `like`/`rcount` counts, the author's `mid`/`uname` (plus `level` and official
verification when present), the message text, and picture URLs. UP-liked marks
(`up_liked`), IP location (`location`) and an inline `pinned` flag appear when
applicable. Avatar/pendant/nameplate/VIP rendering config and other protocol noise —
about 95% of the raw response — is dropped after protocol validation, so the files
stay readable for humans, editors and AI tooling. Pass `--full` to keep the raw
Bilibili comment objects instead; embedded child `replies` previews are removed in
both modes so saved records obey the limit. IDs have a canonical `rpid_str` string
for consumers that cannot represent large integers. Main-comment files are
named `title [BV…].comments.json` (or `.comments.hot.json`); reply threads are
named `title [BV…].comment-ROOT_ID.json`. An existing file is replaced only after
the new document is complete. `result.complete` means the main API signalled its
end, or a thread returned an empty page. A count or short thread page alone does
not prove completion. If the limit stops traversal before this evidence,
`stopped_reason` is `limit` and `complete` is false. Failures and Ctrl+C preserve
the previous file and discard this run's temporary output; Ctrl+C exits with 130.
Downloads do not currently resume. Start/finish timestamps and the first/last
reported totals are included so consumers can recognize a live snapshot.

Bilibili's main-comment total may include child replies; it is not a reliable
denominator for the number of main comments. Counts also change during downloads
and visibility can differ. “All” means all comments enumerated for the current
account during that run. Reported totals and saved-record counts are kept separate,
and the CLI shows actual records/pages rather than a misleading percentage.

### Config file

Save defaults in `config.toml` (in the cookie directory shown above) so you
don't repeat CLI flags every time:

```toml
mode = "a"                      # "all" | "v" | "a" | "s"
proxy = "http://127.0.0.1:7890"
insecure = false
video_dir = "/path/to/videos"
audio_dir = "/path/to/audio"
cookie_dir = "/path/to/cookies"
```

All fields are optional — set only what you need. CLI flags always override
config file values. Override the config path with `--config FILE`. The
`cookie_dir` setting is shared by downloads, `--status`, and `login`; use
`bili-dl login --config FILE` when the configuration itself is stored at a
non-default path. The resolved proxy is likewise shared by downloads and all
Bilibili login/session APIs; `bili-dl login --proxy URL` can override it for a
single login. Set `proxy = ""` to explicitly ignore proxy environment variables.
`bili-dl` does not discover, test, or manage proxies; it only honors the value
the user selected.

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

| Command | Description |
|---------|-------------|
| `bili-dl comments URL` | download all API-visible main comments as JSON |
| `bili-dl comments URL --limit N` | save at most N unique main comments |
| `bili-dl comments URL --sort hot` | use Bilibili's session-bound hot order |
| `bili-dl replies URL ROOT_ID` | download the root and all child replies |
| `bili-dl replies URL ROOT_ID --limit N` | cap the thread at N objects including its root |

Both comment commands also accept `--full` to save raw comment objects (default: lean reading fields).

| Flag | Description |
|------|-------------|
| `--all` | video + audio, merged MP4 + extracted M4A (default) |
| `-v`, `--video` | video only (MP4) |
| `-a`, `--audio` | audio only (M4A, faststart ISOM) |
| `-s`, `--subtitle` | first returned subtitle track only (UTF-8 SRT with timestamps) |
| `--output-dir DIR` | output directory for videos and subtitles |
| `-k`, `--insecure` | skip yt-dlp TLS verification; login/session APIs remain verified |
| `--proxy URL` | proxy for downloads and Bilibili APIs (env: `HTTPS_PROXY`/`HTTP_PROXY`) |
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
  profile or send data to a third party. Media downloads contact the URLs you
  provide through `yt-dlp`; subtitle and comment commands call only Bilibili
  content/CDN endpoints. No telemetry or analytics.
- Browser-imported cookies are backed up before replacement. QR-login cookies
  replace the destination atomically, but only after an online Bilibili
  session check succeeds; a failed check leaves the prior file untouched.
- `auth_state.json` contains Bilibili refresh credentials and a one-way
  Cookie-session fingerprint, including an old credential temporarily kept
  only when server confirmation must be retried. It is therefore Git-ignored
  along with its atomic-write temporary file. It is stored beside the Cookie
  in the per-user config directory; on POSIX its mode is set to `0600`.
- `.auth_state.lock` contains no credentials. It only prevents concurrent
  `bili-dl` processes from interleaving Cookie and refresh-state updates and
  is also Git-ignored.

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

- **Single video only** — media downloads always pass `--no-playlist`, so multi-P
  videos, collections, and favourites are not downloaded as a batch. Give
  each part's URL separately (or list them in a `--batch-file`).
- **Batch downloads are sequential** — no concurrency. A long URL list takes
  proportionally longer; this keeps memory low and avoids hammering Bilibili.
- **Comment snapshots are live and account-visible** — totals may change while a
  run is in progress, and deleted/moderated comments may be counted by Bilibili
  without being enumerable. Hot-order pagination cannot be resumed from its
  offset alone, so an interrupted hot download starts over.
- **Re-downloading overwrites** — no `--no-overwrites` / `--continue` is
  passed to yt-dlp. Running the same URL twice re-downloads and replaces the
  file.
- **Windows CJK filenames** — machine-readable filename prediction and subtitle
  files use explicit UTF-8, preserving Unicode titles independently of the
  terminal's encoding. Subtitle filenames replace filesystem-invalid characters
  and truncate long titles to fit common cross-platform filename limits.

## License

[MIT](LICENSE). `bili-dl` is a wrapper; the actual downloading is done by
[`yt-dlp`](https://github.com/yt-dlp/yt-dlp) (Unlicense) and
[`ffmpeg`](https://ffmpeg.org) (LGPL/GPL), which you must install separately.
