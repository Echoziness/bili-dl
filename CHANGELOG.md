# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.2] - 2026-06-28

### Fixed — Phase 2 returncode regression (friend's machine bug)
v0.1.8 introduced `returncode != 0` as a Phase 2 failure condition. yt-dlp
returns non-zero exit codes for warnings (merge warnings, version diffs,
transient issues) even when the file is written correctly. This caused `[失败]`
on some machines where yt-dlp's exit behaviour differs — the download actually
succeeded but was reported as failure with no detail.

Fix: Phase 2 now only checks `not out_path.exists()`. File exists = success.
Non-zero returncode appends a `[警告]` message but does not block the download
or post-processing. The file is the ground truth; exit codes are advisory.

Phase 1 predict is unchanged (there's no file to check, so returncode +
stdout are needed). (AGENTS.md §2.11 updated.)

### Added
- `test_download_phase2_nonzero_but_file_exists`: returncode≠0 + file exists
  → success with warning. (104 tests total.)

## [0.2.1] - 2026-06-28

### Changed — clig.dev compliance (essential rules)
Audited against [clig.dev](https://clig.dev/) and fixed 4 essential violations.

- **stdout/stderr separation**: all terminal messages now go to `stderr`
  (`ui.*` uses `print(..., file=sys.stderr)`). The primary output is files
  on disk; all text is messaging. `bili-dl URL | grep` is now clean.
- **`NO_COLOR` env var**: `ui._init()` checks `NO_COLOR` (non-empty) and
  `TERM=dumb` to disable colors (no-color.org standard).
- **`--no-color` flag**: explicit `ui.disable_color()` before any output.
- **stdin TTY guard**: if `stdin` is not a TTY and no URL is given, error
  out instead of entering REPL (clig.dev §Interactivity).
- **`HTTP_PROXY`/`HTTPS_PROXY` env vars**: proxy precedence is now
  CLI `--proxy` > config `proxy` > `HTTPS_PROXY` > `HTTP_PROXY` > empty.
- **help text**: added 4 examples + GitHub issues link (clig.dev §Help).

### Added
- 8 new tests (103 total): stdin TTY guard, env var proxy fallback,
  `--no-color` flag, help text validation, stderr assertion.

## [0.2.0] - 2026-06-28

### Added — Two feature modules
- **TOML config file** (`config.toml`): set defaults for `mode`, `proxy`,
  `insecure`, `video_dir`, `audio_dir`, `cookie_dir` without repeating CLI
  flags. CLI flags always override config values. Use `--config FILE` to
  override the config path. New module: `src/bili_dl/settings.py`.
- **Batch download** (`--batch-file FILE`): download a list of URLs from a
  text file (one URL per line, `#` comments). Reports success/failure count;
  exit code 0 if all succeed, 1 if any fail.

### Changed
- **Minimum Python version raised to 3.11** (was 3.9). `tomllib` is stdlib
  since 3.11, preserving the zero-dependency constraint. TOML is used instead
  of INI because it is the Python ecosystem standard (PEP 518/621).
- CI Python matrix: 3.11 + 3.13 (was 3.9 + 3.13).
- `--insecure` and `--proxy` now use `default=None` to distinguish "not
  specified" from "explicitly set", enabling proper config-file fallback.

### Tests
- `tests/test_settings.py` (6 tests): missing/complete/partial/empty config,
  empty-proxy handling, malformed TOML error.
- `tests/test_cli.py` expanded (10 new): config merge precedence, config
  file loading in main(), batch file parsing, batch download success/failure,
  empty batch file.
- Total: 92 tests (up from 76).

## [0.1.9] - 2026-06-28

### Removed — Simplification (KISS regression)
Inspired by Bryan Cantrill's "The Peril of Laziness Lost": LLMs lack the
virtue of laziness — work costs nothing, so they stack more rather than
simplify. This release does the opposite: removes abstractions that made
the system *larger* without making it *simpler*.

- **`MsgLevel` Literal type** deleted. 4 string values don't warrant a
  `Literal` type alias imported across 5 files. `_EMITTERS` runtime
  `KeyError` catches typos; `str` is simpler. (AGENTS.md §2.16)
- **`NavProbeResult` dataclass** deleted. Used in exactly one function and
  one caller — a `tuple[Optional[dict], Optional[str]]` return is lighter.
  The HTTP-vs-network error distinction (v0.1.8) is preserved.
- **25 tautological tests** deleted (101 → 76). These tested Python itself
  (dict lookup, dataclass defaults, `==` operator) not our code. Coverage
  91% → 87% — the lost 4% was zero-signal noise.
- **CI Python matrix** trimmed from 5 versions (3.9-3.13) to 2 (3.9 + 3.13).
  A zero-dependency 500-line package with no version-specific code gets no
  signal from intermediate versions.

### Changed
- `_nav_probe` `except Exception` fallback merged with `URLError` into
  `except (URLError, OSError)` — same behaviour, less code.

## [0.1.8] - 2026-06-28

### Changed — Type safety & error precision
- **mypy strict enforced**: all source files pass `mypy --strict`. CI lint job
  and publish prerequisite now run mypy. `pyproject.toml` `[tool.mypy]` set to
  `strict=true, python_version="3.10"`.
- **`MsgLevel` Literal type**: all `*Result.messages` levels are now
  `Literal["info","ok","warn","error"]` (defined in `config.py`). `cli._EMITTERS`
  typed as `dict[MsgLevel, Callable[[str], None]]`. Typos in message levels are
  caught at type-check time.
- **`_nav_probe` error classification** (AGENTS.md §2.6 follow-up): `except Exception`
  replaced with separate `HTTPError` / `URLError` handlers. Returns
  `NavProbeResult(data, error)` where error is `"network"` or `"http:{status}"`.
  `validate` now reports "HTTP 412（可能被风控）" instead of the misleading
  "网络/SSL 错误" when Bilibili returns an HTTP error.
- **REPL EOFError handling**: `cli._repl` catches `EOFError` from `input()` so
  `bili-dl` exits cleanly when stdin is closed/redirected.

### Added — Test coverage (63% → 91%)
- `tests/test_ffmpeg.py` (10 tests): mock subprocess for repair/extract branches.
- `tests/test_ui.py` (14 tests): _init TTY/VT, colorize ANSI, mode_label.
- `test_downloader.py` expanded (7 new): mock subprocess for download() Phase 1/2.
- `test_cookiestore.py` expanded (8 new): mock urllib for _nav_probe
  success/HTTP-error/URL-error; mock _nav_probe for validate message precision.
- `test_cli.py` expanded (9 new): mock dependencies for main() flow, EOF exit.
- Total: 101 tests (up from 53).

### CI
- Python matrix expanded: 3.9, 3.10, 3.11, 3.12, 3.13 (was 3.9, 3.13).
- New `coverage` job: runs on ubuntu with `coverage report`.
- `lint` job now runs `mypy src/bili_dl` in addition to ruff.
- `publish.yml` test prerequisite now includes mypy.

## [0.1.7] - 2026-06-28

### Changed — Architecture refactoring
- **Layered separation**: logic modules (`cookiesource`, `cookiestore`, `ffmpeg`,
  `downloader`) no longer call `ui.*` directly. They return `*Result` dataclass
  objects with structured `messages: list[tuple[str, str]]`. The controller
  (`cli.py`) is the sole presentation layer, mapping messages to colored output
  via `_emit()`.
- **Module split**: `cookies.py` split into `cookiesource.py` (source detection
  + import) and `cookiestore.py` (validation + `ensure_cookie` orchestration).
  Each module now has a single responsibility.
- **Domain encapsulation**: `cookiestore.ensure_cookie()` encapsulates the
  validate → import → re-validate flow. `cli.py` calls one function instead of
  coordinating internal module details.
- **Parameter object**: `download()` reduced from 11 keyword args to
  `download(url, cfg: DownloadConfig)`. New fields can be added to
  `DownloadConfig` without breaking call sites.

### Added
- `tests/test_cookiesource.py` (12 tests) + `tests/test_cookiestore.py` (7 tests)
  replacing `test_cookies.py`.
- `test_downloader.py` updated for `DownloadConfig` interface (9 tests).
- Total test count: 53 (up from 43).

### Removed
- `cookies.py` (split into `cookiesource.py` + `cookiestore.py`).
- `test_cookies.py` (split into `test_cookiesource.py` + `test_cookiestore.py`).
- Dead code: `suspect_cookie_files()` and `find_ffprobe()` (never called).

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
[0.1.7]: https://github.com/Echoziness/bili-dl/releases/tag/v0.1.7
[0.1.8]: https://github.com/Echoziness/bili-dl/releases/tag/v0.1.8
[0.1.9]: https://github.com/Echoziness/bili-dl/releases/tag/v0.1.9
[0.2.0]: https://github.com/Echoziness/bili-dl/releases/tag/v0.2.0
[0.2.1]: https://github.com/Echoziness/bili-dl/releases/tag/v0.2.1
[0.2.2]: https://github.com/Echoziness/bili-dl/releases/tag/v0.2.2