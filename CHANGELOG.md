# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.6] - 2026-06-28

### Changed
- Version is now sourced dynamically from `__init__.py` via hatchling's
  `dynamic = ["version"]`, eliminating the manual two-place version sync
  that caused the v0.1.1 incident.
- README cookie wording now says "Bilibili-domain entries" instead of
  specifically `.bilibili.com`, matching the actual filter behaviour
  (`www.bilibili.com` entries are kept too).

### Fixed
- nav API probe no longer makes a redundant second request just to fetch
  the username — both `isLogin` and `uname` come from a single call.
- Phase 2 download now checks yt-dlp's return code; a non-zero exit is
  reported as failure even if a partial file was written to disk.

### Added
- Unit tests for `downloader._common_args`, `_template_for`, `_format_for`
  and `cli._build_parser` (previously untested pure logic).
- `test_www_bilibili_kept` asserting `www.bilibili.com` entries are kept.

### CI
- `publish.yml` now requires the test job to pass before publishing to PyPI.

## [0.1.5] - 2026-06-28

### Changed
- Cookie source auto-detection: any `.txt` file containing Bilibili entries is
  automatically recognised — no longer requires a specific filename. The old
  `cookies_all.txt` is still picked up transparently.
- Error message for missing cookies now prints the full target directory path
  and removes the specific browser extension recommendation.

### Removed
- `ALL_COOKIE_FILENAME` constant (no longer needed with auto-detection).

## [0.1.4] - 2026-06-28

### Removed
- Dead constant `MERGED_MODES` from `config.py` (never referenced).

## [0.1.3] - 2026-06-28

### Changed
- Linux: audio download directory changed from `~/.local/share/bili-dl/audio` to `~/Downloads/bilibili_audio`, symmetric with video path.

## [0.1.2] - 2026-06-28

### Fixed
- #HttpOnly_ cookie lines from browser exports (Cookie-Editor etc.) are no longer treated as comments and skipped. SESSDATA with this prefix is now correctly extracted and validated.

## [0.1.1] - 2026-06-27

### Fixed
- Fix version string in `__init__.py` after v0.1.0 release.

## [0.1.0] - 2026-06-27

### Added
- Initial public release.
- Three download modes: `all` (video+audio merged to MP4), `v` (video only),
  `a` (audio only, M4A).
- Cookie management: extract only `.bilibili.com` entries from a
  `cookies_all.txt` Netscape export into a dedicated `cookies_bilibili.txt`,
  with on-disk backup before overwrite.
- Online Cookie validity check via the Bilibili `/x/web-interface/nav` API
  with graceful degradation to local-only validation on network errors.
- Audio container standardization via ffmpeg zero-copy remux
  (`-c:a copy -movflags +faststart`) for both `all` and `a` paths —
  produces `moov`-first ISOM containers friendly to foobar2000 and others.
- Cross-platform path defaults (Windows Videos/Music, macOS Movies/Music,
  Linux `~/Downloads/bilibili_videos` / `~/.local/share/bili-dl/audio`).
- Interactive REPL mode plus one-shot non-interactive mode (`bili-dl <URL>`).
- CLI flags: `--all/-v/-a` modes, `--proxy`, `--insecure/-k`,
  `--output-dir/--audio-dir`, `--cookie-dir`.
- TLS certificate verification enabled by default; opt-out via `-k`.
- MIT-licensed, zero runtime Python dependencies (stdlib only).

### Fixed
- nav API probe now sends a browser User-Agent; previously Bilibili returned
  HTTP 412 to `Python-urllib/x.y` and the broad `except` silently degraded to
  local-only validation, masking the real cause as a "network/SSL error".

### Changed
- `--version` is now exposed as `-V` (capital), since `-v` is taken by
  `--video`. Matches yt-dlp / curl / pip convention.

[0.1.0]: https://github.com/Echoziness/bili-dl/releases/tag/v0.1.0
[0.1.1]: https://github.com/Echoziness/bili-dl/releases/tag/v0.1.1
[0.1.2]: https://github.com/Echoziness/bili-dl/releases/tag/v0.1.2
[0.1.3]: https://github.com/Echoziness/bili-dl/releases/tag/v0.1.3
[0.1.4]: https://github.com/Echoziness/bili-dl/releases/tag/v0.1.4
[0.1.5]: https://github.com/Echoziness/bili-dl/releases/tag/v0.1.5
[0.1.6]: https://github.com/Echoziness/bili-dl/releases/tag/v0.1.6