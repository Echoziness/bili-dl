# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `-s` / `--subtitle` selects subtitle-only downloads in one-shot, batch and
  interactive modes (`s`, also available as the configured default). It saves
  only the first returned track as UTF-8 SRT with cue start/end timestamps,
  respects the selected video part, and supports BV/AV inputs and short links.
- Subtitle downloads reuse the existing login, renewal, proxy and output-dir
  settings without requiring yt-dlp or ffmpeg. No-track, login and request
  failures are reported explicitly; failed downloads preserve existing output.

### Changed

- Split media execution and subtitle fetching behind the common download
  dispatcher and shared config/result contracts. Output directories are prepared
  only for the selected mode; existing media download and post-processing
  behavior remains in the media backend.
- Share CookieJar loading through the HTTP transport for session renewal and
  authenticated content requests. Subtitle CDN requests carry no session cookie.

## [0.4.1] - 2026-09-19

### Fixed

- Decode gzip-compressed Bilibili authentication responses before parsing them.
  The `correspond` renewal endpoint may return gzip even when the client requests
  `identity`; treating those bytes as UTF-8 made an otherwise valid session report
  that Bilibili had not returned a renewal credential.
- Force UTF-8 only for yt-dlp's machine-readable filename prediction. This keeps
  characters outside the Windows system code page, such as `⧸`, aligned with the
  Unicode filename yt-dlp writes to disk without changing terminal encoding or the
  real download process.
- Remove an inherited `PYTHONHOME` only from yt-dlp subprocesses. This prevents
  `uv run bili-dl` from starting an externally installed yt-dlp with uv's different
  Python standard library and failing with `SRE module mismatch`.

## [0.4.0] - 2026-09-11

### Added — Browser-independent QR login and Web-session renewal

- `bili-dl login` opens an isolated Bilibili Web QR-login session. It never
  reads a browser profile or browser cookies, and does not require a specific
  browser to exist on the user's machine.
- QR rendering and the audited RSA-OAEP implementation (`qrcode`,
  `cryptography`) are bundled runtime dependencies. Login and renewal work
  after the normal installation; users do not need to choose or install an
  extra feature package.
- The received Bilibili-only cookie set is checked with `nav` before an
  atomic replacement of `cookies_bilibili.txt`; a failed or unreachable check
  leaves any existing cookie file untouched.
- Successful QR login persists the returned refresh credential separately in
  Git-ignored `auth_state.json`. Before downloading, Bilibili's `cookie/info`
  endpoint is checked at most once per UTC day. If it requests renewal, the
  tool follows `correspond → refresh → confirm`, validates the returned Cookie
  before replacing the old file, then confirms the old refresh credential.
- Renewal state is bound to its Cookie session with a one-way SESSDATA
  fingerprint. Re-login, manual Cookie imports, and partial write failures can
  no longer make the tool send an old refresh token with a different session.
- Download validity and renewal capability are now distinguished: a session
  without `bili_jct` may still download, but it cannot be reported as eligible
  for automatic renewal.
- If confirming the old refresh token fails after a successful refresh, the
  pending confirmation is kept in `auth_state.json` and retried before a later
  daily renewal check instead of being silently forgotten.
- Login and renewal writes are serialized across processes with `filelock`.
  Lock contention skips only the current renewal attempt, so an already valid
  download session is not blocked.
- A failed renewal check never interrupts a currently valid download session
  or triggers an implicit QR prompt. Bilibili can still expire or revoke a
  session, in which case the user explicitly runs `bili-dl login` again.
- `bili-dl --status` shows only user-meaningful state: the logged-in account,
  Bilibili's current renewal requirement, and whether automatic renewal is
  enabled. It needs no download tools and never downloads, prompts, or
  refreshes a session.
- `bili-dl login` now reads `config.toml` (or `--config FILE`) so a configured
  `cookie_dir` is shared consistently by login, status checks, and downloads.

### Changed — Unified authentication transport

- QR login, `nav` validation, renewal checks, refresh, and confirmation now
  use one internal HTTP transport for browser headers, timeouts, Cookie jars,
  JSON decoding, and display-safe error classification. Protocol modules no
  longer maintain subtly different urllib wrappers.
- Proxy resolution is now a single CLI/config/environment policy shared by
  downloads and every Bilibili session API. `bili-dl login` accepts
  `--proxy`; an explicit empty config value still disables environment proxy
  inheritance. yt-dlp now receives its documented empty proxy argument for
  direct connections instead of silently re-reading inherited proxy variables.
- Proxy parameters are keyword-only at the authentication/store boundary so
  future call sites cannot silently confuse transport policy with paths or
  credentials. `--insecure` remains deliberately limited to yt-dlp: login and
  session APIs never disable TLS verification.

### Fixed — Deterministic release artifacts

- The source distribution now uses an explicit allowlist, so local research,
  generated output, `.env` files, Cookies, and authentication state cannot be
  captured from a developer's working tree and uploaded to PyPI.
- The publish workflow now checks package metadata, rejects forbidden sdist
  entries, installs and starts the built wheel, and verifies that the Git tag
  exactly matches the package version before invoking PyPI trusted publishing.

### Tests

- Added protocol and transaction coverage for QR refresh-token receipt,
  refresh-state atomic storage, daily no-refresh checks, staged Cookie
  replacement, session-binding rejection, old-token confirmation,
  transport error safety, and end-to-end proxy propagation (218 tests; total
  coverage 87%, shared transport 96%).

## [0.3.0] - 2026-08-14

### Changed — Line-ending normalization

- Added `.gitattributes` (`* text=auto` + `*.py text eol=lf`) so all Python
  files are committed and checked out as **LF** regardless of platform.
  Previously the repo relied solely on `core.autocrlf=true`, which left the
  working tree in a mixed CRLF/LF state: `git status` reported phantom
  modifications (index stat cached CRLF sizes) that `git diff` showed as
  empty — confusing on both Windows and WSL.
- Renormalized the whole tree (`git add --renormalize`) and rebuilt the two
  stale CRLF files (`src/bili_dl/__init__.py`, `tests/test_paths.py`) as LF.

### Docs — Windows Controlled Folder Access caveat

- Added a README section explaining that Windows Defender CFA silently
  blocks unsigned `ffmpeg`/`yt-dlp` from writing library folders
  (`~/Videos`, `~/Music`), failing with a misleading `Could not write header
  ... No such file or directory`. Includes how to whitelist the binaries and
  the pitfall that the `current` junction path does **not** match CFA's
  resolved real path (AGENTS.md §2.24). No code change — a user's CFA
  setting is a system config, not a product bug; changing default output
  dirs to dodge it would be over-fitting to a machine-specific setup.

### Added — Error diagnosis (self-explaining failures)

- **CFA detection on ffmpeg write failure**: `ffmpeg.py` gains
  `_cfa_enabled()` (registry probe of `EnableControlledFolderAccess`) and
  `_cfa_hint()` (fires only when the target parent dir exists *and* is
  writable *and* CFA is on — the exact "silently disguised denial"
  signature). Both `repair_audio_container` and `extract_audio` now append
  a targeted hint to their failure messages, so a blocked write says
  "可能被 Windows 受控文件夹访问拦截" instead of a bare
  `No such file or directory`.
- **Top-level unexpected errors now print a full stack trace + environment**
  info (`bili-dl <version> | Python <ver> | <platform>`) instead of only the
  exception message — users can copy the complete trace to an issue instead
  of pasting a one-liner.

### Tests

- 8 new tests (160 → 167): `_cfa_enabled` (non-Windows / registry 1 / 0),
  `_cfa_hint` (fires only when blocked, non-Windows, missing parent),
  repair/extract failure messages carry the hint, top-level exception prints
  traceback + env info.
- Coverage stays at 98% (was 98%).

## [0.2.9] - 2026-06-28

### Fixed — Follow-up from third-party review (3 issues)

- **Config `proxy = ""` semantics**: `settings.load` no longer coerces an
  explicit empty proxy string to `None`. A user writing `proxy = ""` in
  `config.toml` means "I want no proxy" — previously `""` was silently
  normalised to `None`, which triggered the environment-variable fallback
  (`HTTPS_PROXY`/`HTTP_PROXY`) and caused the tool to use a system proxy
  despite the explicit disable. Now `""` is preserved through the merge chain
  and blocks the env fallback.
- **Docstring priority**: `cli.py` module docstring now correctly states CLI >
  config > env (matching the actual `_merge_settings` implementation and
  AGENTS.md §2.17). Previously it claimed env took precedence over config,
  which was the opposite of reality.
- **Phase 1 exit-code alignment**: `downloader.py` Phase 1 (predict) no longer
  fails when yt-dlp returns a non-zero exit code but stdout contains a valid
  path — same "trust the artifact, not the exit code" philosophy as Phase 2
  (§2.11). Returncode ≠ 0 now triggers a warning instead of abandoning the
  download.
- **AGENTS.md test count**: synced stale `159→158→159` after removing and
  re-adding tests across two commits.

## [0.2.7] - 2026-06-28

### Fixed — Audit optimisation

After a comprehensive review found a coverage gap (63% vs claimed 87%),
missing CI fail-under gate, and 5 minor issues, every finding was addressed:

- Proxy env vars now accept lowercase (`https_proxy`/`http_proxy`) alongside
  uppercase, matching curl/git/requests convention.
- `settings.load` type-checks `insecure`: non-bool values (e.g. `"yes"`, `1`)
  are coerced to `None` so downstream `cfg.insecure or False` can't pick up a
  truthy string.
- `_extract_sessdata` now matches by Netscape column 6 (`fields[5] ==
  "SESSDATA"`) instead of substring `"SESSDATA" in line` — a cookie value
  containing `"SESSDATA"` would previously have falsely matched.
- `cookiesource`: shared `_first_bili_source()` helper eliminates the
  duplicated scan loop between `find_source` and `import_cookie`.
- `ffmpeg` temp file name gets a random `uuid` suffix, preventing same-stem
  concurrent-repair collisions.

### Changed

- CI: `coverage report --include="src/bili_dl/*" --fail-under=70` gates the
  coverage job — a drop below 70% now fails CI (previously reported without
  enforcing a floor).

### Tests

- Coverage raised from 63% to **98%** (107 → 159 tests). Backfilled branch
  tests for every module: `cli` (+19), `ui` (+11), `ffmpeg` (+8),
  `cookiesource` (+13), `cookiestore` (+8), `downloader` (+3), `settings`
  (+2), `paths` (+2).

## [0.2.6] - 2026-06-28

### Fixed — Audit follow-up (11 issues)
Comprehensive review identified 11 issues across code, tests, docs, and
project metadata. All fixed in this release.

**Code:**
- `ui.prompt`: prompt text now goes to **stderr** (was stdout via `input`).
  The prompt is messaging, not primary output — keeps `bili-dl | grep`
  clean even in the REPL. (clig.dev §Output)
- `cookiestore._nav_probe`: `JSONDecodeError` (B站 returns HTML instead of
  JSON) now classified as `"badjson"`, not lumped into `"network"`.
  `validate` reports "B 站返回非 JSON 内容（可能被风控或接口变更）" instead
  of the misleading "网络/SSL 错误". (AGENTS.md §2.20)
- `cookiesource.import_cookie`: removed duplicate directory glob. The
  candidate scan logic is now in a shared `_candidates()` helper used by
  both `find_source` and `import_cookie`; `import_cookie` no longer calls
  `find_source` then re-globs.

**Metadata:**
- `pyproject.toml`: removed `Python :: 3.14` classifier (CI only tests
  3.11 + 3.13 — declaring an untested version is misleading).

**Docs:**
- README: added **Limitations** section declaring `--no-playlist` (single
  video only), sequential batch downloads, re-download overwrites, and the
  Windows CJK filename trade-off.
- AGENTS.md: synced stale descriptions — `.python-version` (3.9→3.11),
  `uv venv` command (3.9→3.11), §2.16 CI matrix note (added v0.2.0
  3.11+3.13 clarification), §7 release flow (`3平台×5版本`→`3平台×2版本`,
  `curl -k`→`gh release create`).
- AGENTS.md §6: updated SteamTools/MITM note — `gh` CLI is now usable
  directly (curl -k workaround retired, history retained for recurrence).
- CHANGELOG v0.2.4: fixed self-contradictory `--retries 10` wording.

### Added
- `uv.lock` committed for reproducible builds.
- 2 new tests: `test_nav_probe_badjson`, `test_validate_degrades_on_badjson`.
- `test_prompt_reads_stdin` now asserts prompt text goes to stderr.

## [0.2.5] - 2026-06-28

### Fixed
- Cookie directory (`%APPDATA%\bili-dl\` on Windows) is now created at
  startup alongside video/audio directories. Previously the folder didn't
  exist until the user manually created it — making the "put your cookie
  file here" instruction impossible to follow on a fresh install.

## [0.2.4] - 2026-06-28

### Fixed — Comprehensive robustness audit (8 issues)

After a user experienced a bare `[失败]` on a friend's machine with no
way to diagnose the cause, a full robustness audit was performed across
all source files. Every error path now either includes the actual error
detail or degrades gracefully — no bare messages, no unhandled crashes.

**Error messages now include the actual failure reason:**
- Phase 1 predict failure: includes yt-dlp's stderr last line
  (`[失败] 无法获取视频信息: <yt-dlp error>`)
- ffmpeg repair/extract failure: includes ffmpeg's stderr
  (`[失败] 容器修复失败: <ffmpeg error>，保留原文件`)
- File-not-found in repair: includes the path
  (`[失败] 待修复的音频文件不存在: <path>`)

**Unhandled exceptions now caught (no more stack traces):**
- `cookiestore._nav_probe`: `json.JSONDecodeError` caught (B站 returns
  HTML instead of JSON)
- `cli.main`: top-level try/except catches any unexpected exception,
  prints friendly message + issues URL instead of stack trace
- `cli.ensure_dir`: OSError caught (permission denied / disk full /
  path too long)
- `cli._read_batch_urls`: OSError caught (file unreadable)
- `ffmpeg.extract_audio`: `mkdir` OSError caught
- `ffmpeg.repair_audio_container`: `replace`/`stat` OSError caught
  (WinError 32 file locking, permission denied)
- `cookiesource.import_cookie`: `write_text` OSError caught

**Download resilience:**
- yt-dlp now gets `--retries 10` (explicit, ensuring both fragment and
  .part-rename retries are at 10 rather than relying on defaults). More
  chances to recover from transient WinError 32 file locking.

### Tests
- `test_ffmpeg.py` rewritten: mocks `subprocess.run` (was `subprocess.call`),
  tests stderr in error messages, tests mkdir failure.
- 105 tests total (up from 104).

## [0.2.3] - 2026-06-28

### Fixed — Error message UX
Phase 2 failure message changed from bare `[失败]` to
`[失败] 下载未完成 — 请查看上方 yt-dlp 输出获取详细错误信息`.
A bare `[失败]` with no context is hostile UX (clig.dev §Errors:
"Catch errors and rewrite them for humans"). yt-dlp's error output
scrolls by above; the new message directs the user to look there.

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

[Unreleased]: https://github.com/Echoziness/bili-dl/compare/v0.4.1...HEAD
[0.4.1]: https://github.com/Echoziness/bili-dl/compare/v0.4.0...v0.4.1
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
[0.2.3]: https://github.com/Echoziness/bili-dl/releases/tag/v0.2.3
[0.2.4]: https://github.com/Echoziness/bili-dl/releases/tag/v0.2.4
[0.2.5]: https://github.com/Echoziness/bili-dl/releases/tag/v0.2.5
[0.2.6]: https://github.com/Echoziness/bili-dl/releases/tag/v0.2.6
[0.2.7]: https://github.com/Echoziness/bili-dl/releases/tag/v0.2.7
[0.2.8]: https://github.com/Echoziness/bili-dl/releases/tag/v0.2.8
[0.2.9]: https://github.com/Echoziness/bili-dl/releases/tag/v0.2.9
[0.3.0]: https://github.com/Echoziness/bili-dl/releases/tag/v0.3.0
[0.4.0]: https://github.com/Echoziness/bili-dl/releases/tag/v0.4.0
