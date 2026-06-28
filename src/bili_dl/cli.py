"""Command-line interface and interactive REPL for bili-dl.

This is the **controller / presentation layer** — the only module that calls
``ui.*``. All logic modules (cookiesource, cookiestore, ffmpeg, downloader)
return result objects with structured messages; this module translates them
into terminal output via :func:`_emit`.

Modes (mirror the original bd.ps1):
  all — video+audio (DASH, merged to MP4) + extract independent M4A
  v   — video only (single-file MP4 when such a stream exists)
  a   — audio only (M4A, standardised to faststart ISOM)

Both audio-producing paths route the output through ffmpeg zero-copy remux,
so the produced M4A has moov-first layout friendly to foobar2000 etc.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import __version__, cookiestore, downloader, ui
from . import ffmpeg as ff
from .config import VALID_MODES
from .downloader import DownloadConfig
from .paths import config_dir, default_audio_dir, default_video_dir, ensure_dir

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


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bili-dl",
        description="Cross-platform Bilibili downloader (yt-dlp + ffmpeg wrapper).",
        epilog="Run with no URL for an interactive REPL; type `q` to quit.",
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
        help="skip TLS certificate verification (special environments only)",
    )
    p.add_argument(
        "--proxy",
        default="",
        metavar="URL",
        help="proxy URL for yt-dlp (e.g. http://127.0.0.1:7890)",
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
    p.add_argument("url", nargs="?", default=None, help="Bilibili video URL")
    return p


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
    print()
    ui.error("[失败] 没有可用的 B 站 Cookie。")
    ui.warn("  请先在浏览器登录 bilibili.com，然后导出 Cookie（Netscape 格式），")
    ui.warn(f"  将导出的 .txt 文件放入以下目录：{base_dir}")
    ui.warn("  支持任意文件名，只要文件包含 bilibili 条目即可自动识别。")
    print()
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


def _repl(opts: Options, ytdlp: str, ffmpeg_bin: Optional[str]) -> int:
    print(f"使用: {ytdlp}")
    print()
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
            print()
            ui.info(f"[模式] {ui.mode_label(opts.mode)}")
            print()
            continue
        print()
        _run_once(opts, raw.strip(), ytdlp, ffmpeg_bin)
        print()
    print()
    ui.info("再见!")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    opts = Options(
        mode=args.mode or "all",
        proxy=args.proxy,
        insecure=args.insecure,
        cookie_dir=args.cookie_dir,
        video_dir=args.output_dir,
        audio_dir=args.audio_dir,
    )

    # Dependency checks (same severity ladder as bd.ps1) ---------------------
    ytdlp = downloader.find_ytdlp()
    if not ytdlp:
        ui.error("[错误] 未找到 yt-dlp，请先安装 (pip install -U yt-dlp 或 winget install yt-dlp)")
        return 1
    ffmpeg_bin = ff.find_ffmpeg()
    if not ffmpeg_bin:
        ui.warn("[警告] 未找到 ffmpeg，将跳过音频提取与容器修复")

    ensure_dir(opts.video_dir or default_video_dir())
    ensure_dir(opts.audio_dir or default_audio_dir())

    ui.info("B 站视频下载工具")
    print()

    if not _prepare_cookie(opts):
        return 1

    # Non-interactive mode: one shot then exit -------------------------------
    if args.url:
        ui.info(f"使用: {ytdlp}")
        ui.info(f"模式: {ui.mode_label(opts.mode)} | URL: {args.url}")
        print()
        ok = _run_once(opts, args.url, ytdlp, ffmpeg_bin)
        print()
        ui.info("再见!")
        return 0 if ok else 1

    return _repl(opts, ytdlp, ffmpeg_bin)


if __name__ == "__main__":
    raise SystemExit(main())
