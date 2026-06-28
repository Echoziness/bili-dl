"""Command-line interface and interactive REPL for bili-dl.

This is the **controller / presentation layer** — the only module that calls
``ui.*``. All logic modules (cookiesource, cookiestore, ffmpeg, downloader,
settings) return result objects with structured messages; this module
translates them into terminal output via :func:`_emit`.

Configuration precedence (highest to lowest, per clig.dev §Configuration):
  1. CLI flags (``--proxy``, ``-a``, etc.)
  2. ``config.toml`` in the config directory (or ``--config`` path)
  3. Environment variables (``HTTP_PROXY``/``HTTPS_PROXY`` for proxy only;
     ``NO_COLOR`` for colour suppression)
  4. Built-in defaults

Modes:
  all — video+audio (DASH, merged to MP4) + extract independent M4A
  v   — video only (single-file MP4 when such a stream exists)
  a   — audio only (M4A, standardised to faststart ISOM)
"""

from __future__ import annotations

import argparse
import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import __version__, cookiestore, downloader, settings, ui
from . import ffmpeg as ff
from .config import VALID_MODES
from .downloader import DownloadConfig
from .paths import config_dir, config_file_path, default_audio_dir, default_video_dir, ensure_dir

# Encoding policy: we deliberately do NOT force UTF-8 on stdio nor set
# PYTHONUTF8. yt-dlp and Python both emit using the host's default locale
# (cp936 on a stock Windows console, UTF-8 on Linux/mac). Keeping both sides
# on the same locale means the predicted path (phase 1) and the file yt-dlp
# actually writes (phase 2) decode to identical strings, so ``out_path.exists()``
# stays reliable for CJK titles. Forcing UTF-8 here would mismatch yt-dlp's
# cp936 output and silently break downloads of any non-ASCII title — verified
# the hard way during initial bring-up.

# ─── Presentation: map logic-module message levels to ui functions ─────────
_EMITTERS = {
    "info": ui.info,
    "ok": ui.ok,
    "warn": ui.warn,
    "error": ui.error,
}


def _emit(messages: list[tuple[str, str]]) -> None:
    """Print structured messages from logic modules via the appropriate ui function."""
    for level, text in messages:
        _EMITTERS[level](text)


@dataclass
class Options:
    mode: str = "all"
    proxy: str = ""
    insecure: bool = False
    cookie_dir: Optional[Path] = None
    video_dir: Optional[Path] = None
    audio_dir: Optional[Path] = None


_HELP_EPILOG = """\
examples:
  bili-dl https://www.bilibili.com/video/BV...     # video + audio (default)
  bili-dl -a https://www.bilibili.com/video/BV...  # audio only (M4A)
  bili-dl --batch-file urls.txt                    # batch download
  bili-dl                                          # interactive REPL

report issues: https://github.com/Echoziness/bili-dl/issues\
"""


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bili-dl",
        description="Cross-platform Bilibili downloader (yt-dlp + ffmpeg wrapper).",
        epilog=_HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--all",
        action="store_const",
        const="all",
        dest="mode",
        help="download video + audio (default)",
    )
    mode.add_argument(
        "-v", "--video", action="store_const", const="v", dest="mode", help="download video only"
    )
    mode.add_argument(
        "-a",
        "--audio",
        action="store_const",
        const="a",
        dest="mode",
        help="download audio only (M4A)",
    )
    # NOTE: -v is taken by --video; expose --version via -V. Both yt-dlp and
    # curl use the same convention (-V / --version), so users won't be surprised.
    p.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"bili-dl {__version__}",
        help="show version and exit",
    )
    p.add_argument(
        "-k",
        "--insecure",
        action="store_true",
        default=None,
        help="skip TLS certificate verification (special environments only)",
    )
    p.add_argument(
        "--no-color",
        action="store_true",
        help="disable colored output (also disabled by NO_COLOR env var or non-TTY)",
    )
    p.add_argument(
        "--proxy",
        default=None,
        metavar="URL",
        help="proxy URL for yt-dlp (env: HTTP_PROXY, HTTPS_PROXY)",
    )
    p.add_argument(
        "--cookie-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help=f"override cookie directory (default: {config_dir()})",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="override video output directory",
    )
    p.add_argument(
        "--audio-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="override audio output directory",
    )
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="FILE",
        help=f"override config file path (default: {config_file_path()})",
    )
    p.add_argument(
        "--batch-file",
        type=Path,
        default=None,
        metavar="FILE",
        help="download URLs listed in a text file (one URL per line, # comments)",
    )
    p.add_argument("url", nargs="?", default=None, help="Bilibili video URL")
    return p


def _load_settings(config_path: Optional[Path]) -> settings.Settings:
    """Load config.toml, returning empty Settings on missing file."""
    path = config_path or config_file_path()
    try:
        return settings.load(path)
    except tomllib.TOMLDecodeError as e:
        ui.warn(f"[警告] 配置文件 {path} 解析失败: {e}")
        ui.warn("[警告] 忽略配置文件，使用命令行参数和默认值")
        return settings.Settings()


def _merge_settings(args: argparse.Namespace, cfg: settings.Settings) -> Options:
    """Merge config + env vars with CLI args.

    Precedence: CLI flags > env vars > config file > defaults.
    (clig.dev §Configuration)
    """
    mode = args.mode or cfg.mode or "all"

    # Proxy: CLI flag > config > env vars (clig.dev standard).
    # Accept both upper and lower case — curl/git/requests all check both.
    proxy = args.proxy if args.proxy is not None else cfg.proxy
    if proxy is None:
        proxy = (
            os.environ.get("HTTPS_PROXY")
            or os.environ.get("https_proxy")
            or os.environ.get("HTTP_PROXY")
            or os.environ.get("http_proxy")
            or ""
        )

    return Options(
        mode=mode if mode in VALID_MODES else "all",
        proxy=proxy,
        insecure=args.insecure if args.insecure is not None else (cfg.insecure or False),
        cookie_dir=args.cookie_dir or cfg.cookie_dir,
        video_dir=args.output_dir or cfg.video_dir,
        audio_dir=args.audio_dir or cfg.audio_dir,
    )


def _read_batch_urls(path: Path) -> list[str]:
    """Read URLs from a batch file, skipping blank lines and # comments."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    urls = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            urls.append(stripped)
    return urls


def _prepare_cookie(opts: Options) -> bool:
    """Ensure a valid Bilibili cookie is available.

    Delegates the validate → import → re-validate flow to
    :func:`cookiestore.ensure_cookie` (one call, no internal coordination
    leaking into the controller). On failure, prints a help block.
    """
    result = cookiestore.ensure_cookie(opts.cookie_dir)
    _emit(result.messages)
    if result.ready:
        return True

    base_dir = opts.cookie_dir or config_dir()
    print(file=sys.stderr)
    ui.error("[失败] 没有可用的 B 站 Cookie。")
    ui.warn("  请先在浏览器登录 bilibili.com，然后导出 Cookie（Netscape 格式），")
    ui.warn(f"  将导出的 .txt 文件放入以下目录：{base_dir}")
    ui.warn("  支持任意文件名，只要文件包含 bilibili 条目即可自动识别。")
    print(file=sys.stderr)
    return False


def _run_once(opts: Options, url: str, ytdlp: str, ffmpeg_bin: Optional[str]) -> bool:
    """Execute one download and emit results. Returns success."""
    cfg = DownloadConfig(
        mode=opts.mode,
        video_dir=opts.video_dir or default_video_dir(),
        audio_dir=opts.audio_dir or default_audio_dir(),
        cookie_path=cookiestore.bili_cookie_path(opts.cookie_dir),
        proxy=opts.proxy,
        insecure=opts.insecure,
        ytdlp=ytdlp,
        ffmpeg_bin=ffmpeg_bin,
    )
    result = downloader.download(url, cfg)
    _emit(result.messages)
    return result.success


def _batch_download(opts: Options, urls: list[str], ytdlp: str, ffmpeg_bin: Optional[str]) -> int:
    """Download multiple URLs sequentially. Returns 0 if all succeed, 1 if any fail."""
    total = len(urls)
    ui.info(f"[批量] 共 {total} 个链接")
    failures = 0
    for i, url in enumerate(urls, 1):
        print(file=sys.stderr)
        ui.info(f"[批量] ({i}/{total}) {url}")
        if not _run_once(opts, url, ytdlp, ffmpeg_bin):
            failures += 1
    print(file=sys.stderr)
    if failures:
        ui.warn(f"[批量] 完成: {total - failures} 成功, {failures} 失败")
    else:
        ui.ok(f"[批量] 全部完成: {total} 个链接")
    return 1 if failures else 0


def _repl(opts: Options, ytdlp: str, ffmpeg_bin: Optional[str]) -> int:
    print(f"使用: {ytdlp}", file=sys.stderr)
    print(file=sys.stderr)
    while True:
        label = ui.mode_label(opts.mode)
        try:
            raw = ui.prompt(f"输入 B 站链接 (模式:{label} | all/v/a 切换 | q 退出): ")
        except EOFError:
            break
        if not raw.strip():
            continue
        if raw.strip().lower() == "q":
            break
        if raw.strip().lower() in VALID_MODES:
            opts.mode = raw.strip().lower()
            print(file=sys.stderr)
            ui.info(f"[模式] {ui.mode_label(opts.mode)}")
            print(file=sys.stderr)
            continue
        print(file=sys.stderr)
        _run_once(opts, raw.strip(), ytdlp, ffmpeg_bin)
        print(file=sys.stderr)
    print(file=sys.stderr)
    ui.info("再见!")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    try:
        return _main_impl(argv)
    except Exception as e:
        ui.error(f"[错误] 发生未预期错误: {e}")
        ui.info("请将以上错误信息提交到 https://github.com/Echoziness/bili-dl/issues")
        return 1


def _main_impl(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Handle --no-color before anything else (clig.dev §Output) --------------
    if args.no_color:
        ui.disable_color()

    # Load config file, merge with CLI args + env vars (CLI > env > config) --
    cfg = _load_settings(args.config)
    opts = _merge_settings(args, cfg)

    # Dependency checks (same severity ladder as bd.ps1) ---------------------
    ytdlp = downloader.find_ytdlp()
    if not ytdlp:
        ui.error("[错误] 未找到 yt-dlp，请先安装 (pip install -U yt-dlp 或 winget install yt-dlp)")
        return 1
    ffmpeg_bin = ff.find_ffmpeg()
    if not ffmpeg_bin:
        ui.warn("[警告] 未找到 ffmpeg，将跳过音频提取与容器修复")

    try:
        ensure_dir(opts.cookie_dir or config_dir())
        ensure_dir(opts.video_dir or default_video_dir())
        ensure_dir(opts.audio_dir or default_audio_dir())
    except OSError as e:
        ui.error(f"[错误] 无法创建输出目录: {e}")
        ui.info("请检查路径权限或使用 --output-dir / --audio-dir 指定其他目录")
        return 1

    ui.info("B 站视频下载工具")
    print(file=sys.stderr)

    if not _prepare_cookie(opts):
        return 1

    # Batch mode: read URLs from file, download all --------------------------
    if args.batch_file:
        if not args.batch_file.exists():
            ui.error(f"[错误] 批量文件不存在: {args.batch_file}")
            return 1
        try:
            urls = _read_batch_urls(args.batch_file)
        except OSError as e:
            ui.error(f"[错误] 无法读取批量文件: {e}")
            return 1
        if not urls:
            ui.warn("[警告] 批量文件中没有有效 URL")
            return 0
        ui.info(f"使用: {ytdlp}")
        ui.info(f"模式: {ui.mode_label(opts.mode)} | 批量文件: {args.batch_file}")
        print(file=sys.stderr)
        return _batch_download(opts, urls, ytdlp, ffmpeg_bin)

    # Non-interactive mode: one URL then exit --------------------------------
    if args.url:
        ui.info(f"使用: {ytdlp}")
        ui.info(f"模式: {ui.mode_label(opts.mode)} | URL: {args.url}")
        print(file=sys.stderr)
        ok = _run_once(opts, args.url, ytdlp, ffmpeg_bin)
        print(file=sys.stderr)
        ui.info("再见!")
        return 0 if ok else 1

    # No URL and no batch file: REPL only if stdin is a TTY ------------------
    # (clig.dev §Interactivity: "Only use prompts if stdin is a TTY")
    if not sys.stdin.isatty():
        ui.error("[错误] 非交互模式需要提供 URL 或 --batch-file 参数")
        ui.info("用法: bili-dl <URL>  或  bili-dl --batch-file <FILE>")
        return 1

    return _repl(opts, ytdlp, ffmpeg_bin)


if __name__ == "__main__":
    raise SystemExit(main())
