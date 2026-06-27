# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.2] - 2026-06-28

### Fixed
- #HttpOnly_ cookie lines from browser exports (Cookie-Editor etc.) are no longer treated as comments and skipped. SESSDATA with this prefix is now correctly extracted and validated.

[0.1.1]: https://github.com/Echoziness/bili-dl/releases/tag/v0.1.1
[Unreleased]:

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
  XDG `~/.local/share/bili-dl` elsewhere).
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

[0.1.2] - 2026-06-28

### Fixed
- #HttpOnly_ cookie lines from browser exports (Cookie-Editor etc.) are no longer treated as comments and skipped. SESSDATA with this prefix is now correctly extracted and validated.

[0.1.1]: https://github.com/Echoziness/bili-dl/releases/tag/v0.1.1
[Unreleased]:: https://github.com/Echoziness/bili-dl/releases/tag/v0.1.0